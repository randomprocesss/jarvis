import type { NextConfig } from "next";

// Static export: `npm run build` writes the pages to out/, which the server serves at /.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  devIndicators: false,
};

export default nextConfig;
