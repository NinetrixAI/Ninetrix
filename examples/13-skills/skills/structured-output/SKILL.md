---
name: structured-output
version: 1.0.0
description: Ensure all responses follow the expected output format
author: ninetrix
tags: [output, json, formatting]
requires:
  tools: []
---

# Structured Output

Your responses MUST follow the output format defined by the system. This is non-negotiable.

## Before responding

1. Check if an output schema is defined (output_type in your configuration)
2. If yes — your final response must be valid JSON matching that schema exactly
3. If no schema is defined — respond in clear, well-organized prose

## When a schema is defined

- Output ONLY the JSON object — no markdown fences, no explanation before or after
- Include every required field, even if the value is empty or null
- Use the correct types: strings are strings, numbers are numbers, arrays are arrays
- Validate your output mentally before sending: does every required field exist? Are types correct?

## Common mistakes to avoid

- Do NOT wrap JSON in ```json ... ``` code fences
- Do NOT add a preamble like "Here is the result:"
- Do NOT omit optional fields that you have data for — include them
- Do NOT use string "null" when the schema expects an actual null
- Do NOT return an array when the schema expects an object (or vice versa)

## When you're unsure about a field

- Use the most reasonable default for the type (empty string for strings, 0 for numbers, [] for arrays)
- Never skip a required field — a reasonable default is better than a missing field
