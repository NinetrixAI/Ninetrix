import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  basePath: "/dashboard",
  images: { unoptimized: true },
};

export default nextConfig;
