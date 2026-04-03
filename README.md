# Smart Notes — Claude Code Implementation Prompt

## Mission
You are building a semantic clustering and AI chat layer on top of **Blinko**, an open-source self-hosted notes web app. The goal is to extend Blinko so that notes are automatically grouped into named categories (clusters) using embeddings, and users can ask natural language questions about their notes via an AI chat interface.

---

## Phase 0 — Codebase Discovery (do this before writing a single line)

Before touching any code, run the following exploration steps and summarise your findings in a `PLAN.md` file at the root of the repo. Do not skip this phase.

### 0.1 — Understand the project structure
```bash
find . -type f -name "*.ts" -o -name "*.tsx" | grep -v node_modules | grep -v .next | head -80
cat package.json
cat prisma/schema.prisma
```

Look for and document:
- Framework version (Next.js app router vs pages router)
- State management library in use (check for MobX, Zustand, Redux)
- How API routes are structured (`/app/api/` or `/pages/api/`)
- How auth/session is handled (NextAuth, Lucia, custom)
- Where AI/embedding calls currently live (search for `openai`, `embed`, `ollama`, `ai`)
- How the existing RAG search works end-to-end
- The Prisma schema — what models exist, relations, and field types

### 0.2 — Trace a note from creation to storage
Follow the data flow for creating a note:
1. Find the UI component that renders the note input
2. Find the API route or server action it calls
3. Find the Prisma `create` call
4. Find where (if anywhere) embeddings are generated on save

Document this flow in `PLAN.md` with file paths and line numbers.

### 0.3 — Understand the existing vector/search setup
Search for how Blinko currently does semantic search:
```bash
grep -r "embed\|vector\|similarity\|pgvector\|lancedb" --include="*.ts" --include="*.tsx" -l
```
Read those files. Understand what embedding model is used, how vectors are stored, and how search queries are made. This is the foundation your clustering will build on.

### 0.4 — Map the frontend component tree
Find and read:
- The main layout component
- The notes list/feed component
- Any existing sidebar or navigation
- Any existing AI chat or search UI

Document where your new Cluster View and AI Chat UI will slot in without breaking existing layouts.

---

## Phase 1 — Database Schema

**Only proceed after Phase 0 is complete and documented in `PLAN.md`.**

Add the following to `prisma/schema.prisma`. Adapt field names to match the existing naming conventions you found in Phase 0.

```prisma
model Cluster {
  id          Int       @id @default(autoincrement())
  name        String                        // AI-generated name e.g. "API Keys", "Meeting Notes"
  description String?                       // optional 1-sentence summary
  color       String?                       // hex color for UI badge
  createdAt   DateTime  @default(now())
  updatedAt   DateTime  @updatedAt
  notes       NoteCluster[]
}

model NoteCluster {
  noteId      Int
  clusterId   Int
  score       Float                         // cosine similarity score, useful for ranking
  note        Note      @relation(fields: [noteId], references: [id], onDelete: Cascade)
  cluster     Cluster   @relation(fields: [clusterId], references: [id], onDelete: Cascade)

  @@id([noteId, clusterId])
}
```

> **Check**: make sure `Note` matches the actual model name in the existing schema. It may be called `Blinko` or something else.

Run:
```bash
npx prisma migrate dev --name add_clusters
npx prisma generate
```

---

## Phase 2 — Embedding Pipeline

### 2.1 — Embedding utility
Create `lib/embeddings.ts`. Use the same embedding provider already configured in Blinko (check what API keys exist in `.env`). If the project uses OpenAI, use `text-embedding-3-small`. If it uses Ollama, use `nomic-embed-text`.

```typescript
// lib/embeddings.ts
// Wrap the existing embedding logic or provider already used in the codebase.
// Do NOT introduce a new provider — reuse what is already configured.
// Export: embedText(text: string): Promise<number[]>
```

### 2.2 — Hook into note save
Find the exact location where a note is saved to the database. After the Prisma `create` or `upsert` call, add an async background call (do not await it — don't block the response) to generate and store the embedding for the new note.

Pattern:
```typescript
// fire-and-forget — don't block the API response
generateAndStoreEmbedding(note.id, note.content).catch(console.error)
```

---

## Phase 3 — Clustering Engine

Create `lib/clustering.ts`.

### Algorithm
1. Fetch all notes that have embeddings from the database
2. Run k-means clustering on the embedding vectors
   - Start with `k = Math.max(3, Math.round(Math.sqrt(noteCount / 2)))` — this scales naturally
   - Use a simple k-means implementation (install `ml-kmeans` — lightweight, no heavy deps)
3. For each cluster, collect the 5 most central notes (closest to centroid)
4. Call Claude to name the cluster:

```typescript
const prompt = `You are organizing a personal notes app. 
Here are ${samples.length} notes that were grouped together by semantic similarity:

${samples.map((n, i) => `Note ${i + 1}: ${n.content.slice(0, 300)}`).join('\n\n')}

Give this cluster a short, specific name (2-4 words max) that a developer would use.
Examples of good names: "API Keys", "Meeting Notes Q1", "React Code Snippets", "Deployment Configs"
Respond with ONLY the cluster name, nothing else.`
```

5. Persist each cluster and its member notes to the database using the `Cluster` and `NoteCluster` models
6. Expose a function: `runClustering(): Promise<void>`

### Trigger strategy
- Run clustering automatically when a user saves their **10th note, then every 5 notes after that**
- Also expose a manual trigger via API so the user can force a re-cluster from the UI
- Track the last cluster run in a simple `Setting` key-value record if one exists in the schema, or use a plain JSON file as fallback

---

## Phase 4 — API Routes

Create the following routes, following the exact pattern of existing API routes in the codebase (match the file structure, error handling style, and auth middleware already in use):

### `POST /api/clusters/run`
Triggers clustering manually. Protected — require the same auth check already used in other routes. Returns `{ ok: true, clusterCount: number }`.

### `GET /api/clusters`
Returns all clusters with their member note IDs and count. Used by the frontend cluster view.

### `GET /api/clusters/[id]`
Returns a single cluster with its full member notes.

---

## Phase 5 — RAG Chat API

Create `app/api/chat/route.ts` (or `pages/api/chat.ts` depending on what router you found in Phase 0).

```typescript
// POST /api/chat
// Body: { message: string }
// 
// 1. Embed the user's message using the same embedText() utility
// 2. Query the vector store for the top 8 most similar notes
//    (use the same vector similarity query already in the codebase)
// 3. Build a context string from the retrieved notes
// 4. Call Claude (claude-sonnet-4-6) with:
//    - System: "You are a personal assistant with access to the user's notes.
//               Answer questions using only the provided notes as context.
//               If the answer is not in the notes, say so clearly."
//    - User: the retrieved notes as context + the user's question
// 5. Stream the response back using the Vercel AI SDK if already present,
//    otherwise return as a plain JSON response
```

---

## Phase 6 — Frontend

**Read the existing UI components carefully before building anything.** Match the design system, component patterns, and styling conventions exactly (Tailwind classes, component library, icon set).

### 6.1 — Cluster sidebar / panel
- Add a "Clusters" section to the existing sidebar navigation
- Each cluster renders as a badge with its AI-generated name and a note count
- Clicking a cluster filters the main notes feed to show only notes in that cluster
- Add a small "Re-cluster" button that calls `POST /api/clusters/run` and shows a loading spinner

### 6.2 — AI Chat panel
- Add a chat icon/button to the main UI (match where other action buttons live)
- Opens a slide-over or bottom panel (match the existing modal/drawer patterns in the codebase)
- Simple chat UI: message input at bottom, conversation thread above
- Each assistant response should cite which notes it pulled from (show note titles as small chips below the response)
- Calls `POST /api/chat` and streams or displays the response

### 6.3 — Note card cluster badge
- On each note card, show a small colored badge with the cluster name it belongs to
- If a note has no cluster yet, show nothing (don't break the existing card layout)

---

## Implementation Rules

Follow these strictly throughout:

1. **Explore before you build.** Never assume a file path, model name, or API shape. Read the actual code first.
2. **One phase at a time.** Complete and verify each phase before starting the next. After each phase, run `npm run build` to catch TypeScript errors.
3. **Never break existing features.** The existing Blinko note capture, search, and AI features must keep working. Run the app after each phase and test the core flow.
4. **Match existing patterns.** If Blinko uses a specific error response shape, use it. If it uses a specific auth middleware, use it. Don't introduce parallel patterns.
5. **No heavy new dependencies** unless absolutely necessary. Prefer what's already in `package.json`. The only acceptable new packages are `ml-kmeans` (clustering) and nothing else unless you hit a genuine blocker.
6. **Environment variables.** Never hardcode API keys. Check `.env.example` for the naming convention and add any new vars there with comments.
7. **Write a short test** for the clustering function — at minimum, verify that `runClustering()` produces at least one named cluster from a set of 10 seeded notes.
8. **Document as you go.** Keep `PLAN.md` updated. After each phase, add a "Phase N — Done" section with what was implemented, any deviations from this plan, and any gotchas discovered.

---

## Definition of Done

The feature is complete when:
- [ ] A user can save notes and clusters appear automatically in the sidebar after the 10th note
- [ ] Cluster names are meaningful (AI-generated, not "Cluster 1")
- [ ] A user can click a cluster and see only the notes in that cluster
- [ ] A user can type a question like "what was the Stripe API key I saved?" and get a grounded answer
- [ ] The existing Blinko features (note save, search, auth) are unbroken
- [ ] `npm run build` passes with no TypeScript errors
- [ ] `PLAN.md` is complete and accurate

# LovablePPTX Clone

This project is a web-based clone of the Lovable.dev interface for generating PowerPoint presentations using an AI agent.

## Prerequisites

1.  **Node.js** (v18+)
2.  **Python** (3.10+)
3.  **Anthropic API Key**

## Setup

1.  **Clone the repository** (if not already done).
2.  **Install dependencies**:
    ```bash
    # Install Python dependencies
    pip install -r requirements.txt
    
    # Install Frontend dependencies
    cd frontend && npm install
    cd ..
    
    # Install pptxgenjs globally or in root
    npm install pptxgenjs
    ```
3.  **Set up Environment Variables**:
    Create a `.env` file in the root directory:
    ```
    ANTHROPIC_API_KEY=sk-ant-api03-...
    ```
4.  **Skills**:
    Ensure the `skills` directory exists and contains the necessary skills (especially `pptx`).
    If not, clone them:
    ```bash
    git clone https://github.com/anthropics/skills ./skills
    ```

## Running the Application

You can use the provided script to start both backend and frontend:

```bash
chmod +x start.sh
./start.sh
```

Or run them manually:

**Backend:**
```bash
uvicorn backend.main:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) to use the app.

## How it works

1.  The frontend sends a prompt to the backend API.
2.  The backend invokes `agent.py` which uses LangChain and Anthropic Claude.
3.  The agent follows instructions from the `pptx` skill to generate a Node.js script.
4.  The script uses `pptxgenjs` to create a `.pptx` file.
5.  The backend returns the file URL to the frontend for download.
