FROM node:24-alpine AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:24-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
RUN addgroup --system --gid 1001 mnemonic && adduser --system --uid 1001 --ingroup mnemonic mnemonic
COPY --from=builder --chown=mnemonic:mnemonic /app/.next/standalone ./
COPY --from=builder --chown=mnemonic:mnemonic /app/.next/static ./.next/static
COPY --from=builder --chown=mnemonic:mnemonic /app/public ./public
USER mnemonic
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 CMD node -e "fetch('http://127.0.0.1:3000/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server.js"]
