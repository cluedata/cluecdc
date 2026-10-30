"use client";

import { PageHeader } from "@/components/common";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { SourceEditor } from "./source-editor";

export function SourceCreatePage() {
  const router = useRouter();
  return (
    <>
      <Link className="back-link" href="/sources">
        <ArrowLeft size={14} />
        All sources
      </Link>
      <PageHeader
        title="Add source"
        description="Choose a database source, validate CDC readiness, then discover tables or collections."
        eyebrow="DATA MOVEMENT / SOURCE"
      />
      <SourceEditor
        fullPage
        open
        onOpenChange={() => router.push("/sources")}
      />
    </>
  );
}
