import defaultMdxComponents from "fumadocs-ui/mdx";
import { source } from "@/lib/source";
import {
  DocsPage,
  DocsBody,
  DocsTitle,
  DocsDescription,
} from "fumadocs-ui/page";
import { notFound } from "next/navigation";
import { Mermaid } from "fumadocs-mermaid/ui";
import { Callout } from "fumadocs-ui/components/callout";
import type { MDXContent } from "mdx/types";
import type { TableOfContents } from "fumadocs-core/server";

interface MdxPageData {
  title: string;
  description?: string;
  body: MDXContent;
  toc: TableOfContents;
  full?: boolean;
}

export default async function Page(props: {
  params: Promise<{ slug?: string[] }>;
}) {
  const params = await props.params;
  const page = source.getPage(params.slug);
  if (!page) notFound();

  const data = page.data as unknown as MdxPageData;
  const MDX = data.body;

  return (
    <DocsPage toc={data.toc} full={data.full}>
      <DocsTitle>{data.title}</DocsTitle>
      <DocsDescription>{data.description}</DocsDescription>
      <DocsBody>
        <MDX
          components={{
            ...defaultMdxComponents,
            Mermaid,
            Callout,
          }}
        />
      </DocsBody>
    </DocsPage>
  );
}

export function generateStaticParams() {
  return source.generateParams();
}

export async function generateMetadata(props: {
  params: Promise<{ slug?: string[] }>;
}) {
  const params = await props.params;
  const page = source.getPage(params.slug);
  if (!page) notFound();

  const data = page.data as unknown as MdxPageData;

  return {
    title: data.title,
    description: data.description,
  };
}
