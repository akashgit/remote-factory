import { docs } from "@/.source";
import { loader } from "fumadocs-core/source";

const fumadocsSource = docs.toFumadocsSource();

// fumadocs-mdx v11 returns files as a function; fumadocs-core v15 expects an array
const files =
  typeof fumadocsSource.files === "function"
    ? (fumadocsSource.files as unknown as () => typeof fumadocsSource.files)()
    : fumadocsSource.files;

export const source = loader({
  baseUrl: "/docs",
  source: { files },
});
