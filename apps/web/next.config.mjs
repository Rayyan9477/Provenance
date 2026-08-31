/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  typescript: { ignoreBuildErrors: false },
  eslint: { ignoreDuringBuilds: false },

  // Emit .next/standalone: server.js plus exactly the node_modules the traced
  // server code reaches, instead of the whole dependency tree.
  //
  // Added 2026-08-27 for the Cloud Run image (deploy/Dockerfile.web). It is
  // load-bearing there rather than an optimisation: without it the runtime
  // stage has no server.js to copy and the build fails at the COPY. Setting it
  // here rather than passing a flag keeps `next build` producing the same
  // output locally and in the image, so a build that works on a laptop is the
  // build that ships.
  //
  // Local `next dev` and `next start` are unaffected: standalone changes what
  // `build` writes, not how the dev server runs.
  output: "standalone",

  // .next/standalone is traced from this directory. Next infers a root by
  // walking up for a lockfile, and this repository has one at apps/web and a
  // package.json at the workspace root — an ambiguity Next resolves by warning
  // and guessing. Naming it removes the guess, and a wrong guess here silently
  // traces the wrong module set.
  outputFileTracingRoot: new URL(".", import.meta.url).pathname,
};

export default nextConfig;
