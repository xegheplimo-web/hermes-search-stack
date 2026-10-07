"use client";

import {
  MarkdownTextPrimitive,
  useIsMarkdownCodeBlock,
} from "@assistant-ui/react-markdown";
import { Check, Copy } from "lucide-react";
import remarkGfm from "remark-gfm";
import {
  memo,
  useState,
  type ComponentPropsWithoutRef,
  type FC,
} from "react";

import { cn } from "@/lib/utils";

type HastNode = {
  type?: string;
  tagName?: string;
  value?: string;
  properties?: { className?: string | string[] };
  children?: HastNode[];
};

function hastText(node: HastNode | undefined): string {
  if (!node) return "";
  if (node.type === "text") return node.value ?? "";
  return (node.children ?? []).map(hastText).join("");
}

function CopyTextButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.cssText = "position:fixed;opacity:0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <button
      type="button"
      onClick={copy}
      className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      aria-label="Copy code"
    >
      {copied ? <Check size={13} /> : <Copy size={13} />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

type PreProps = ComponentPropsWithoutRef<"pre"> & { node?: HastNode };

const CodeBlock: FC<PreProps> = ({ node, children, className, ...props }) => {
  const codeNode = node?.children?.find(
    (c) => c.type === "element" && c.tagName === "code",
  );
  const raw = hastText(codeNode).replace(/\n$/, "");
  const cls = codeNode?.properties?.className;
  const lang =
    (Array.isArray(cls) ? cls : cls ? [cls] : [])
      .find((c) => c.startsWith("language-"))
      ?.slice(9) ?? "code";

  return (
    <div className="my-3 overflow-hidden rounded-xl border border-border">
      <div className="flex items-center justify-between border-b border-border bg-muted px-3 py-1.5">
        <span className="font-mono text-xs text-muted-foreground">{lang}</span>
        <CopyTextButton text={raw} />
      </div>
      <pre
        {...props}
        className={cn(
          "overflow-x-auto bg-code-bg p-4 font-mono text-[13px] leading-relaxed text-code-fg",
          className,
        )}
      >
        {children}
      </pre>
    </div>
  );
};

type CodeProps = ComponentPropsWithoutRef<"code"> & { node?: HastNode };

const Code: FC<CodeProps> = ({ className, children, ...props }) => {
  const isBlock = useIsMarkdownCodeBlock();
  if (isBlock) {
    return (
      <code {...props} className={cn("font-mono", className)}>
        {children}
      </code>
    );
  }
  return (
    <code
      {...props}
      className={cn(
        "rounded-md bg-muted px-1.5 py-0.5 font-mono text-[0.85em]",
        className,
      )}
    >
      {children}
    </code>
  );
};

const MarkdownTextImpl: FC = () => (
  <MarkdownTextPrimitive
    className="aui-md"
    remarkPlugins={[remarkGfm]}
    smooth
    components={{
      pre: CodeBlock,
      code: Code,
      a: ({ className, ...props }) => (
        <a
          {...props}
          target="_blank"
          rel="noreferrer noopener"
          className={cn(
            "font-medium text-blue-600 underline underline-offset-2 dark:text-blue-400",
            className,
          )}
        />
      ),
    }}
  />
);

export const MarkdownText = memo(MarkdownTextImpl);
