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
