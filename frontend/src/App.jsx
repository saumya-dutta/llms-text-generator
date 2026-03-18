import { useState, useRef } from "react";

const API_BASE = "";  // empty = use Vite proxy; set to "http://localhost:8000" if running standalone

export default function App() {
  const [url, setUrl] = useState("");
  const [output, setOutput] = useState("");
  const [status, setStatus] = useState("idle"); // idle | loading | success | error
  const [errorMsg, setErrorMsg] = useState("");
  const [copied, setCopied] = useState(false);
  const textareaRef = useRef(null);

  async function handleGenerate(e) {
    e.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;

    setStatus("loading");
    setOutput("");
    setErrorMsg("");

    try {
      const res = await fetch(`${API_BASE}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: trimmed }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `Server error (${res.status})`);
      }

      const text = await res.text();
      setOutput(text);
      setStatus("success");
    } catch (err) {
      setErrorMsg(err.message || "Something went wrong.");
      setStatus("error");
    }
  }

  async function handleCopy() {
    if (!output) return;
    await navigator.clipboard.writeText(output);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function handleDownload() {
    if (!output) return;
    const blob = new Blob([output], { type: "text/plain" });
    const href = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = href;
    a.download = "llms.txt";
    a.click();
    URL.revokeObjectURL(href);
  }

  return (
    <div className="page">
      {/* Header */}
      <header className="header">
        <div className="logo">
          <span className="logo-bracket">&lt;</span>
          llms.txt by saumya
          <span className="logo-bracket">/&gt;</span>
        </div>
        <p className="tagline">
          Generate an <code>llms.txt</code> file for any website
        </p>
      </header>

      {/* Main */}
      <main className="main">
        <form className="input-row" onSubmit={handleGenerate}>
          <div className="input-wrapper">
            <span className="input-icon">🔗</span>
            <input
              type="url"
              className="url-input"
              placeholder="https://example.com"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={status === "loading"}
              required
            />
          </div>
          <button
            type="submit"
            className="generate-btn"
            disabled={status === "loading" || !url.trim()}
          >
            {status === "loading" ? (
              <>
                <span className="spinner" />
                Generating…
              </>
            ) : (
              "Generate"
            )}
          </button>
        </form>

        {/* Error */}
        {status === "error" && (
          <div className="error-banner">
            <span className="error-icon">⚠</span>
            {errorMsg}
          </div>
        )}

        {/* Output */}
        {status === "success" && output && (
          <div className="output-section">
            <div className="output-header">
              <span className="output-label">llms.txt</span>
              <div className="output-actions">
                <button className="action-btn" onClick={handleCopy}>
                  {copied ? "✓ Copied" : "Copy"}
                </button>
                <button className="action-btn primary" onClick={handleDownload}>
                  Download
                </button>
              </div>
            </div>
            <pre className="output-box" ref={textareaRef}>
              {output}
            </pre>
          </div>
        )}
      </main>
    </div>
  );
}
