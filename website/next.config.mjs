import { createMDX } from "fumadocs-mdx/next";
import { remarkMdxMermaid } from "fumadocs-mermaid";

const withMDX = createMDX({
  mdxOptions: {
    remarkPlugins: [remarkMdxMermaid],
  },
});

/** @type {import('next').NextConfig} */
const config = {
  output: "export",
  basePath: "/remote-factory",
  assetPrefix: "/remote-factory",
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
};

export default withMDX(config);
