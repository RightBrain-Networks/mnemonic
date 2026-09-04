import type { NextConfig } from "next";

function liveSyncDestination(): string {
  const base = new URL(process.env.MNEMONIC_API_URL ?? "http://api:8000");
  if (
    !["http:", "https:"].includes(base.protocol)
    || base.username
    || base.password
    || base.pathname !== "/"
    || base.search
    || base.hash
  ) {
    throw new Error("MNEMONIC_API_URL must be an HTTP(S) origin.");
  }
  return new URL("/api/v1/sync", base).toString();
}

const nextConfig: NextConfig = {
  output: "standalone",
  compress: false,
  agentRules: false,
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "X-DNS-Prefetch-Control", value: "off" },
        { key: "Referrer-Policy", value: "no-referrer" },
        { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        { key: "Content-Security-Policy", value: "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'" }
      ]
    }];
  },
  async rewrites() {
    return {
      beforeFiles: [
        { source: "/api/mnemonic/sync", destination: liveSyncDestination() }
      ],
      afterFiles: [],
      fallback: []
    };
  }
};

export default nextConfig;
