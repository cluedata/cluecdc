import type { NextConfig } from "next";
const config: NextConfig = {
  output: "standalone",
  transpilePackages: ["@cluecdc/ui", "@cluecdc/contracts"],
  poweredByHeader: false,
};
export default config;
