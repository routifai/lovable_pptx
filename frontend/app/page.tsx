"use client";

import { useState, useRef, useMemo } from "react";
import {
  Send,
  Download,
  Loader2,
  Sparkles,
  FileText,
  CheckCircle2,
  Wrench,
  ChevronRight,
  Layers,
  AlertCircle,
} from "lucide-react";

type OutlineItem = { index: number; title: string; layout: string };

type PlanEvent = {
  type: "plan";
  deck_title: string;
  slide_count: number;
  theme: {
    palette: Record<string, string>;
    fonts: Record<string, string>;
    layout_size: string;
    motif: string;
  };
  outline: OutlineItem[];
};

type SlideStarted = {
  type: "slide_started";
  index: number;
  total: number;
  title: string;
};

type SlideDone = {
  type: "slide_done";
  index: number;
  total: number;
  elapsed_s?: number;
};

type SlideFailed = {
  type: "slide_failed";
  index: number;
  total: number;
  error: string;
  elapsed_s?: number;
};

type StreamEvent =
  | { type: "status"; message: string }
  | { type: "tool_call"; tool: string; message: string }
  | { type: "tool_result"; tool: string; message: string }
  | { type: "agent_message"; message: string }
  | PlanEvent
  | SlideStarted
  | SlideDone
  | SlideFailed
  | { type: "done"; file: string | null; message: string }
  | { type: "error"; message: string };

type SlideStatus = "pending" | "in_progress" | "done" | "failed";

interface LogEntry {
  id: number;
  event: StreamEvent;
}

export default function Home() {
  const [prompt, setPrompt] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [finalFile, setFinalFile] = useState<string | null>(null);
  const [isDone, setIsDone] = useState(false);
  const [plan, setPlan] = useState<PlanEvent | null>(null);
  const [slideStatus, setSlideStatus] = useState<Record<number, SlideStatus>>({});
  const logRef = useRef<HTMLDivElement>(null);
  const idRef = useRef(0);

  const progress = useMemo(() => {
    if (!plan) return { done: 0, failed: 0, total: 0, percent: 0 };
    const total = plan.slide_count || 0;
    const values = Object.values(slideStatus);
    const done = values.filter((s) => s === "done").length;
    const failed = values.filter((s) => s === "failed").length;
    const percent = total > 0 ? Math.round(((done + failed) / total) * 100) : 0;
    return { done, failed, total, percent };
  }, [plan, slideStatus]);

  const appendLog = (event: StreamEvent) => {
    setLog((prev) => {
      const next = [...prev, { id: idRef.current++, event }];
      return next;
    });
    setTimeout(() => {
      logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
    }, 50);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim()) return;

    setIsLoading(true);
    setLog([]);
    setFinalFile(null);
    setIsDone(false);
    setPlan(null);
    setSlideStatus({});

    try {
      const response = await fetch("http://localhost:8000/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt }),
      });

      if (!response.ok || !response.body) {
        throw new Error("Failed to connect to generation API");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const event: StreamEvent = JSON.parse(line.slice(6));
              appendLog(event);

              if (event.type === "plan") {
                setPlan(event);
                const init: Record<number, SlideStatus> = {};
                for (const s of event.outline) init[s.index] = "pending";
                setSlideStatus(init);
              } else if (event.type === "slide_started") {
                setSlideStatus((prev) => ({ ...prev, [event.index]: "in_progress" }));
              } else if (event.type === "slide_done") {
                setSlideStatus((prev) => ({ ...prev, [event.index]: "done" }));
              } else if (event.type === "slide_failed") {
                setSlideStatus((prev) => ({ ...prev, [event.index]: "failed" }));
              } else if (event.type === "done") {
                setFinalFile(event.file);
                setIsDone(true);
                setIsLoading(false);
              } else if (event.type === "error") {
                setIsLoading(false);
              }
            } catch {}
          }
        }
      }
    } catch (err: any) {
      appendLog({ type: "error", message: err.message || "Connection failed" });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen relative overflow-hidden flex flex-col font-sans selection:bg-blue-500/30">
      {/* Background blobs */}
      <div className="absolute inset-0 z-0 pointer-events-none">
        <div className="absolute top-[-20%] left-[-10%] w-[70%] h-[70%] rounded-full bg-blue-600/20 blur-[120px]" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[60%] h-[60%] rounded-full bg-yellow-500/10 blur-[100px]" />
        <div className="absolute top-[20%] right-[10%] w-[400px] h-[400px] bg-gradient-to-br from-pink-500/10 to-transparent blur-[80px] rounded-full opacity-60" />
      </div>

      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-white/5 z-10 relative bg-black/20 backdrop-blur-md">
        <div className="flex items-center gap-2">
          <div className="bg-gradient-to-tr from-blue-500 to-yellow-400 p-1.5 rounded-lg">
            <Sparkles className="w-5 h-5 text-white" />
          </div>
          <span className="font-bold text-lg tracking-tight text-white">LovablePPTX</span>
        </div>
        <div className="text-sm text-zinc-400 font-medium">Powered by Agent.py</div>
      </header>

      {/* Main */}
      <main className="flex-1 flex flex-col items-center justify-center p-4 sm:p-8 max-w-4xl mx-auto w-full z-10 relative">
        {/* Hero */}
        <div className="text-center mb-10 space-y-4">
          <h1 className="text-5xl sm:text-6xl font-extrabold tracking-tight text-white drop-shadow-sm">
            What do you want to{" "}
            <span className="bg-gradient-to-r from-blue-400 to-yellow-300 bg-clip-text text-transparent">
              build?
            </span>
          </h1>
          <p className="text-zinc-400 text-lg sm:text-xl max-w-2xl mx-auto leading-relaxed">
            Describe your presentation topic, and our AI agent will craft a professional slide deck for you.
          </p>
        </div>

        {/* Input */}
        <div className="w-full max-w-2xl relative group">
          <div className="absolute -inset-0.5 bg-gradient-to-r from-blue-500 to-yellow-500 rounded-2xl opacity-30 group-hover:opacity-50 transition duration-500 blur-md" />
          <form
            onSubmit={handleSubmit}
            className="relative flex flex-col bg-[#0a0a0a]/90 backdrop-blur-xl border border-white/10 rounded-2xl p-2 shadow-2xl transition-all duration-300 hover:border-white/20"
          >
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Create a pitch deck for a new AI startup..."
              className="w-full bg-transparent text-white placeholder-zinc-500 resize-none outline-none p-4 text-lg min-h-[140px]"
              disabled={isLoading}
            />
            <div className="flex justify-between items-center px-2 pb-2 mt-2">
              <div className="text-xs font-medium px-2 flex items-center gap-2">
                {isLoading ? (
                  <span className="text-blue-400 animate-pulse flex items-center gap-1.5">
                    <Loader2 className="w-3 h-3 animate-spin" /> Agent is working...
                  </span>
                ) : isDone ? (
                  <span className="text-green-400 flex items-center gap-1.5">
                    <CheckCircle2 className="w-3 h-3" /> Done
                  </span>
                ) : (
                  <span className="text-zinc-500">Ready to create</span>
                )}
              </div>
              <button
                type="submit"
                disabled={isLoading || !prompt.trim()}
                className="flex items-center gap-2 bg-white text-black hover:bg-zinc-200 disabled:opacity-50 disabled:hover:bg-white px-5 py-2.5 rounded-xl font-bold transition-all transform active:scale-95 shadow-lg shadow-white/5"
              >
                {isLoading ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <>
                    <span>Generate</span>
                    <Send className="w-4 h-4" />
                  </>
                )}
              </button>
            </div>
          </form>
        </div>

        {/* Live Stream Log */}
        {log.length > 0 && (
          <div className="w-full max-w-2xl mt-8 space-y-4">
            {/* Plan + per-slide progress (parallel pipeline) */}
            {plan && (
              <div className="bg-[#0e0e0e]/80 border border-white/10 rounded-2xl p-5 backdrop-blur-md shadow-xl space-y-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <div className="p-2 bg-gradient-to-tr from-blue-500/30 to-yellow-400/20 rounded-xl">
                      <Layers className="w-5 h-5 text-blue-300" />
                    </div>
                    <div>
                      <div className="font-bold text-white text-base leading-tight">
                        {plan.deck_title}
                      </div>
                      <div className="text-xs text-zinc-500 mt-0.5">
                        {plan.slide_count} slides · {plan.theme.layout_size} · motif:{" "}
                        <span className="text-zinc-400 italic">{plan.theme.motif}</span>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {["primary", "secondary", "accent"].map((k) =>
                      plan.theme.palette[k] ? (
                        <div
                          key={k}
                          title={`${k}: #${plan.theme.palette[k]}`}
                          className="w-5 h-5 rounded-full border border-white/20 shadow-inner"
                          style={{ backgroundColor: `#${plan.theme.palette[k]}` }}
                        />
                      ) : null
                    )}
                  </div>
                </div>

                <div>
                  <div className="flex items-center justify-between text-xs text-zinc-400 mb-2 font-mono">
                    <span>
                      {progress.done}/{progress.total} rendered
                      {progress.failed > 0 ? (
                        <span className="text-red-400">  ·  {progress.failed} failed</span>
                      ) : null}
                    </span>
                    <span>{progress.percent}%</span>
                  </div>
                  <div className="h-2 bg-white/5 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-gradient-to-r from-blue-500 to-yellow-400 transition-all duration-300"
                      style={{ width: `${progress.percent}%` }}
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 max-h-56 overflow-y-auto pr-1">
                  {plan.outline.map((slide) => {
                    const status = slideStatus[slide.index] ?? "pending";
                    return (
                      <SlideRow
                        key={slide.index}
                        index={slide.index}
                        title={slide.title}
                        layout={slide.layout}
                        status={status}
                      />
                    );
                  })}
                </div>
              </div>
            )}

            {/* Download card on completion */}
            {isDone && finalFile && (
              <div className="bg-[#111]/80 border border-white/10 rounded-2xl p-5 flex flex-col sm:flex-row items-center justify-between gap-4 backdrop-blur-md shadow-xl animate-in fade-in slide-in-from-bottom-4 duration-500">
                <div className="flex items-center gap-4">
                  <div className="p-3 bg-blue-500/20 rounded-2xl">
                    <FileText className="w-7 h-7 text-blue-400" />
                  </div>
                  <div className="text-left">
                    <h3 className="font-bold text-white text-base">Presentation Ready</h3>
                    <p className="text-zinc-400 text-sm">{finalFile}</p>
                  </div>
                </div>
                <a
                  href={`http://localhost:8000/api/download/${finalFile}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 bg-blue-600 hover:bg-blue-500 text-white px-5 py-2.5 rounded-xl font-bold transition-colors shadow-lg shadow-blue-900/20"
                >
                  <Download className="w-4 h-4" />
                  Download PPTX
                </a>
              </div>
            )}

            {/* Agent log */}
            <div
              ref={logRef}
              className="bg-black/40 border border-white/5 rounded-2xl p-4 font-mono text-sm backdrop-blur-sm max-h-80 overflow-y-auto space-y-2 scroll-smooth"
            >
              <div className="flex items-center gap-2 mb-3 pb-2 border-b border-white/5">
                <div className={`w-2 h-2 rounded-full ${isLoading ? "bg-blue-400 animate-pulse" : isDone ? "bg-green-400" : "bg-zinc-500"}`} />
                <span className="text-xs font-bold text-zinc-500 uppercase tracking-wider">Agent Log</span>
              </div>

              {log.map(({ id, event }) => (
                <LogLine key={id} event={event} />
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

function SlideRow({
  index,
  title,
  layout,
  status,
}: {
  index: number;
  title: string;
  layout: string;
  status: SlideStatus;
}) {
  const dot =
    status === "done" ? (
      <CheckCircle2 className="w-3.5 h-3.5 text-green-400 shrink-0" />
    ) : status === "in_progress" ? (
      <Loader2 className="w-3.5 h-3.5 text-blue-400 animate-spin shrink-0" />
    ) : status === "failed" ? (
      <AlertCircle className="w-3.5 h-3.5 text-red-400 shrink-0" />
    ) : (
      <div className="w-2 h-2 rounded-full bg-zinc-700 ml-1 mr-1" />
    );
  return (
    <div className="flex items-center gap-2 text-xs text-zinc-300 bg-black/30 rounded-lg px-2.5 py-1.5 border border-white/5">
      {dot}
      <span className="font-mono text-zinc-500 w-6 shrink-0">#{index}</span>
      <span className="truncate flex-1">{title}</span>
      <span className="text-[10px] uppercase tracking-wider text-zinc-600 font-semibold shrink-0">
        {layout.replace(/_/g, " ")}
      </span>
    </div>
  );
}

function LogLine({ event }: { event: StreamEvent }) {
  if (event.type === "status") {
    return (
      <div className="flex items-start gap-2 text-zinc-400">
        <ChevronRight className="w-3.5 h-3.5 mt-0.5 shrink-0 text-zinc-600" />
        <span>{event.message}</span>
      </div>
    );
  }

  if (event.type === "plan") {
    return (
      <div className="flex items-start gap-2 text-blue-300">
        <Layers className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span>
          Plan ready: <span className="font-semibold">{event.deck_title}</span> ·{" "}
          {event.slide_count} slides
        </span>
      </div>
    );
  }

  if (event.type === "slide_started") {
    return (
      <div className="flex items-start gap-2 text-zinc-500">
        <Loader2 className="w-3.5 h-3.5 mt-0.5 shrink-0 animate-spin text-blue-400" />
        <span>
          Slide {event.index}/{event.total}: <span className="text-zinc-300">{event.title}</span>
        </span>
      </div>
    );
  }

  if (event.type === "slide_done") {
    return (
      <div className="flex items-start gap-2 text-green-400">
        <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span>
          Slide {event.index}/{event.total} rendered
          {event.elapsed_s !== undefined ? (
            <span className="text-zinc-500"> · {event.elapsed_s}s</span>
          ) : null}
        </span>
      </div>
    );
  }

  if (event.type === "slide_failed") {
    return (
      <div className="flex items-start gap-2 text-red-400">
        <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span>
          Slide {event.index}/{event.total} failed: <span className="text-zinc-400">{event.error}</span>
        </span>
      </div>
    );
  }

  if (event.type === "tool_call") {
    return (
      <div className="flex items-start gap-2 text-yellow-400">
        <Wrench className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span>
          <span className="text-yellow-300 font-semibold">{event.tool}</span>
          <span className="text-zinc-400"> — calling tool</span>
        </span>
      </div>
    );
  }

  if (event.type === "tool_result") {
    return (
      <div className="flex items-start gap-2 text-zinc-400">
        <span className="text-zinc-600 shrink-0 mt-0.5">↩</span>
        <span className="text-zinc-500 leading-relaxed">{event.message}</span>
      </div>
    );
  }

  if (event.type === "agent_message") {
    return (
      <div className="flex items-start gap-2 text-white mt-1 pt-1 border-t border-white/5">
        <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0 text-green-400" />
        <span className="leading-relaxed whitespace-pre-wrap">{event.message}</span>
      </div>
    );
  }

  if (event.type === "done") {
    return (
      <div className="flex items-start gap-2 text-green-400 font-semibold">
        <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span>{event.message}</span>
      </div>
    );
  }

  if (event.type === "error") {
    return (
      <div className="flex items-start gap-2 text-red-400">
        <span className="shrink-0">✗</span>
        <span>{event.message}</span>
      </div>
    );
  }

  return null;
}
