# Visora web app (Next.js, standalone server). On Vercel this file is not used.
#   docker build --build-arg CC_API_ORIGIN=http://api:8000 -t context-commerce-web web
FROM node:22-alpine AS build
WORKDIR /web
ENV NEXT_TELEMETRY_DISABLED=1 CC_STANDALONE=1
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# /api/* and /auth/google/* are proxied to this origin; it is fixed at build time (Next.js rewrites).
ARG CC_API_ORIGIN=http://api:8000
ENV CC_API_ORIGIN=${CC_API_ORIGIN}
RUN npm run build

FROM node:22-alpine
WORKDIR /web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
ARG CC_API_ORIGIN=http://api:8000
ENV CC_API_ORIGIN=${CC_API_ORIGIN}
COPY --from=build /web/.next/standalone ./
COPY --from=build /web/.next/static ./.next/static
COPY --from=build /web/public ./public
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:3000/login || exit 1
CMD ["node", "server.js"]
