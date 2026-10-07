import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Dev-only: the app may be reached as localhost or 127.0.0.1; without this,
  // Next blocks HMR/asset requests whose Origin host differs from the
  // server-init host.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;
