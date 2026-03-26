/**
 * Baileys Bridge — WhatsApp Web sidecar for Ninetrix agents.
 *
 * Connects to WhatsApp via @whiskeysockets/baileys and communicates
 * with the Python WhatsAppAdapter over a Unix Domain Socket using
 * newline-delimited JSON.
 *
 * Protocol (← = bridge→python, → = python→bridge):
 *   ← {"type":"qr","data":"qr-string"}
 *   ← {"type":"connected","data":{"id":"123@s.whatsapp.net","name":"Bot"}}
 *   ← {"type":"message","data":{"chat_id":"123@s.whatsapp.net","user_id":"123","username":"John","text":"hello"}}
 *   → {"type":"send","chat_id":"123@s.whatsapp.net","text":"Hi there!"}
 *   ← {"type":"sent","status":"ok"}
 *   ← {"type":"disconnected","reason":"..."}
 *   ← {"type":"error","message":"..."}
 *
 * Env vars:
 *   BAILEYS_SOCKET_PATH  — UDS path (default: /var/run/whatsapp.sock)
 *   BAILEYS_AUTH_DIR     — auth state directory (default: /data/whatsapp)
 */

const net = require('net');
const fs = require('fs');
const path = require('path');
const { makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');
const pino = require('pino');

const SOCKET_PATH = process.env.BAILEYS_SOCKET_PATH || '/var/run/whatsapp.sock';
const AUTH_DIR = process.env.BAILEYS_AUTH_DIR || '/data/whatsapp';

const logger = pino({ level: 'warn' });

let sock = null;
let udsClient = null;
// Track message IDs sent BY THE AGENT to prevent echo loops in self-chat.
const sentByAgent = new Set();
// Connected user's phone number (without @s.whatsapp.net) — used to resolve LIDs
let connectedPhone = '';
// Map LID → phone number for resolving WhatsApp's new LID format
const lidToPhone = new Map();

function send(obj) {
  if (udsClient && !udsClient.destroyed) {
    udsClient.write(JSON.stringify(obj) + '\n');
  }
}

async function startWhatsApp() {
  // Ensure auth directory exists
  fs.mkdirSync(AUTH_DIR, { recursive: true });

  console.log('[baileys] Loading auth state...');
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

  // Use Baileys' built-in default version instead of fetching from WhatsApp servers.
  // fetchLatestBaileysVersion() makes an HTTP call that can be slow or hang.
  let version;
  try {
    const result = await Promise.race([
      fetchLatestBaileysVersion(),
      new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 5000)),
    ]);
    version = result.version;
    console.log(`[baileys] WA Web version: ${version.join('.')} (fetched)`);
  } catch {
    version = [2, 3000, 1015901307];
    console.log(`[baileys] Using default WA Web version (fetch skipped)`);
  }

  console.log('[baileys] Connecting to WhatsApp...');
  sock = makeWASocket({
    version,
    auth: state,
    logger,
    printQRInTerminal: false,
    generateHighQualityLinkPreview: false,
    markOnlineOnConnect: true,
  });

  // Save credentials on every update (Signal keys rotate per message)
  sock.ev.on('creds.update', saveCreds);

  // Connection state changes
  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      send({ type: 'qr', data: qr });
    }

    if (connection === 'open') {
      const id = sock.user?.id || '';
      const name = sock.user?.name || '';
      // Extract phone number from JID (e.g. "1234567890:41@s.whatsapp.net" → "1234567890")
      connectedPhone = id.split('@')[0].split(':')[0];
      // Map our own LID to our phone (will be populated from contact sync too)
      const lid = sock.user?.lid;
      if (lid) {
        const lidNum = lid.split('@')[0].split(':')[0];
        lidToPhone.set(lidNum, connectedPhone);
        console.log(`[baileys] LID mapping: ${lidNum} → ${connectedPhone}`);
      }
      send({ type: 'connected', data: { id, name, phone: connectedPhone } });
      console.log(`[baileys] Connected: ${name} (${id}) phone=${connectedPhone}`);
    }

    if (connection === 'close') {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const reason = DisconnectReason[statusCode] || `status ${statusCode}`;

      if (statusCode === DisconnectReason.loggedOut) {
        // Session expired / device removed — need to re-pair
        send({ type: 'disconnected', reason: 'logged_out' });
        console.log('[baileys] Logged out — clearing auth files (not the mount point)');
        // Clear auth FILES inside the directory, not the directory itself
        // (it's a Docker volume mount point — rmdir would fail with EBUSY)
        try {
          const files = fs.readdirSync(AUTH_DIR);
          for (const file of files) {
            const filePath = path.join(AUTH_DIR, file);
            fs.rmSync(filePath, { recursive: true, force: true });
          }
        } catch (e) {
          console.log(`[baileys] Warning: failed to clear auth files: ${e.message}`);
        }
        // Do NOT auto-reconnect on logout — the user needs to re-pair
        console.log('[baileys] Device was removed by WhatsApp. Re-pair with: ninetrix channel connect whatsapp');
        // Exit cleanly — the ChannelManager will NOT restart a logged-out session
        process.exit(1);
      } else {
        send({ type: 'disconnected', reason });
        console.log(`[baileys] Disconnected: ${reason} — reconnecting in 5s`);
        setTimeout(startWhatsApp, 5000);
      }
    }
  });

  // Build LID → phone mapping from contact sync events
  sock.ev.on('contacts.upsert', (contacts) => {
    for (const contact of contacts) {
      if (contact.lid && contact.id) {
        const lidNum = contact.lid.split('@')[0].split(':')[0];
        const phone = contact.id.split('@')[0].split(':')[0];
        if (lidNum && phone && lidNum !== phone) {
          lidToPhone.set(lidNum, phone);
        }
      }
    }
    if (lidToPhone.size > 0) {
      console.log(`[baileys] LID→phone mappings: ${lidToPhone.size} contacts`);
    }
  });

  // Incoming messages — handle both 'notify' (normal) and 'append' (self-chat, sync)
  sock.ev.on('messages.upsert', (upsert) => {
    const messages = upsert.messages || [];
    const updateType = upsert.type;

    console.log(`[baileys] messages.upsert: type=${updateType}, count=${messages.length}`);
    for (const m of messages) {
      console.log(`[baileys]   key: fromMe=${m.key.fromMe} remoteJid=${m.key.remoteJid} id=${m.key.id}`);
      console.log(`[baileys]   messageType: ${Object.keys(m.message || {}).join(', ') || '(empty)'}`);
      console.log(`[baileys]   timestamp: ${m.messageTimestamp}`);
    }

    // Accept all upsert types — 'notify' (new) and 'append' (self-chat, sync)
    const isAppend = updateType === 'append' || updateType !== 'notify';

    for (const msg of messages) {
      // Skip status broadcasts
      if (msg.key.remoteJid === 'status@broadcast') continue;

      // Skip protocol/system messages (no actual message content)
      if (!msg.message) continue;

      // For 'append' messages (self-chat), skip old ones from history sync
      if (isAppend) {
        const msgTs = Number(msg.messageTimestamp || 0);
        const now = Math.floor(Date.now() / 1000);
        if (msgTs > 0 && (now - msgTs) > 30) {
          continue; // older than 30 seconds — history sync, skip
        }
      }

      // Skip agent's own replies (prevents echo loops in self-chat).
      // We track IDs of messages sent via sendMessage() — only skip those.
      // User's self-chat messages (fromMe=true but NOT sent by bridge) pass through.
      if (msg.key.id && sentByAgent.has(msg.key.id)) {
        sentByAgent.delete(msg.key.id);
        continue;
      }

      // Extract text from various message types
      let text = '';
      if (msg.message?.conversation) {
        text = msg.message.conversation;
      } else if (msg.message?.extendedTextMessage?.text) {
        text = msg.message.extendedTextMessage.text;
      } else if (msg.message?.imageMessage?.caption) {
        text = `[image] ${msg.message.imageMessage.caption}`;
      } else if (msg.message?.videoMessage?.caption) {
        text = `[video] ${msg.message.videoMessage.caption}`;
      } else if (msg.message?.documentMessage) {
        text = `[document] ${msg.message.documentMessage.fileName || 'file'}`;
      } else {
        // Log skipped message types for debugging
        const msgType = Object.keys(msg.message || {}).join(', ');
        console.log(`[baileys] Skipping non-text message type: ${msgType} from ${msg.key.remoteJid}`);
        continue;
      }

      text = text.trim();
      if (!text) continue;

      const chatId = msg.key.remoteJid;       // ORIGINAL JID — used for replies (keeps Signal session intact)
      const userId = msg.key.participant || chatId;
      const username = msg.pushName || userId.split('@')[0];

      // Resolve LID → phone for allowed_ids matching ONLY (don't change chat_id).
      // WhatsApp uses @lid internally but users configure allowed_ids with phone numbers.
      let phone = '';
      if (chatId.endsWith('@lid')) {
        const lidNum = chatId.split('@')[0].split(':')[0];
        phone = lidToPhone.get(lidNum) || '';
        if (!phone && msg.key.fromMe && connectedPhone) {
          phone = connectedPhone;
        }
        if (phone) {
          console.log(`[baileys] LID ${lidNum} → phone ${phone} (for matching only, reply goes to LID)`);
        }
      } else if (chatId.endsWith('@s.whatsapp.net')) {
        phone = chatId.split('@')[0].split(':')[0];
      }

      console.log(`[baileys] Message from ${username} (${chatId}, phone=${phone || '?'}): ${text.substring(0, 80)}`);

      send({
        type: 'message',
        data: { chat_id: chatId, user_id: userId, username, text, phone },
      });
    }
  });
}

// Handle incoming commands from Python over UDS
function handleCommand(line) {
  let cmd;
  try {
    cmd = JSON.parse(line);
  } catch {
    return;
  }

  if (cmd.type === 'send' && sock) {
    const chatId = cmd.chat_id;
    const text = cmd.text || '';

    // Send to the ORIGINAL JID (LID or phone) — do NOT resolve LID → phone.
    // Resolving would create a second Signal session that conflicts with the
    // LID session, corrupting decryption after the first exchange.
    if (chatId && text) {
      console.log(`[baileys] Sending to ${chatId} (${text.length} chars)`);
      sock.sendMessage(chatId, { text })
        .then((sent) => {
          // Track the message ID so we skip it in messages.upsert (anti-echo)
          if (sent?.key?.id) {
            sentByAgent.add(sent.key.id);
            // Clean up after 60s to prevent memory leak
            setTimeout(() => sentByAgent.delete(sent.key.id), 60000);
          }
          send({ type: 'sent', status: 'ok' });
        })
        .catch((err) => send({ type: 'error', message: err.message }));
    }
  } else if (cmd.type === 'ping') {
    send({ type: 'pong' });
  } else if (cmd.type === 'quit') {
    process.exit(0);
  }
}

// Start UDS server — Python connects to this
function startServer() {
  // Remove stale socket file
  if (fs.existsSync(SOCKET_PATH)) {
    fs.unlinkSync(SOCKET_PATH);
  }

  const server = net.createServer((client) => {
    console.log('[baileys] Python adapter connected');
    udsClient = client;

    let buffer = '';
    client.on('data', (chunk) => {
      buffer += chunk.toString();
      const lines = buffer.split('\n');
      buffer = lines.pop(); // keep incomplete line in buffer
      for (const line of lines) {
        if (line.trim()) handleCommand(line.trim());
      }
    });

    client.on('close', () => {
      console.log('[baileys] Python adapter disconnected');
      udsClient = null;
    });

    client.on('error', (err) => {
      console.error('[baileys] UDS error:', err.message);
    });
  });

  server.listen(SOCKET_PATH, () => {
    console.log(`[baileys] UDS server listening on ${SOCKET_PATH}`);
    // Ensure socket file is accessible
    fs.chmodSync(SOCKET_PATH, 0o666);
    // Start WhatsApp connection
    startWhatsApp();
  });

  // Cleanup on exit
  process.on('SIGINT', () => {
    if (sock) sock.end();
    server.close();
    process.exit(0);
  });
  process.on('SIGTERM', () => {
    if (sock) sock.end();
    server.close();
    process.exit(0);
  });
}

startServer();
