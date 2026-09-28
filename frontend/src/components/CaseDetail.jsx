import { useEffect, useState } from "react";
import { getCase, reviewCase, sendChatMessage } from "../api.js";

const TERMINAL_STATUSES = new Set(["Closed"]);

export default function CaseDetail({ caseId, onClosed }) {
  const [caseData, setCaseData] = useState(null);
  const [error, setError] = useState(null);
  const [reviewer, setReviewer] = useState("");
  const [notes, setNotes] = useState("");
  const [editing, setEditing] = useState(false);
  const [editedDecision, setEditedDecision] = useState("");
  const [editedRationale, setEditedRationale] = useState("");
  const [editedCitations, setEditedCitations] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [chatLog, setChatLog] = useState([]);
  const [chatInput, setChatInput] = useState("");

  const reload = () => {
    setError(null);
    getCase(caseId)
      .then((data) => {
        setCaseData(data);
        setEditedDecision(data.recommendation?.decision || "");
        setEditedRationale(data.recommendation?.rationale || "");
        setEditedCitations((data.recommendation?.cited_guidelines || []).join(", "));
      })
      .catch((err) => setError(err.message));
  };

  useEffect(reload, [caseId]);

  async function submitReview(action) {
    if (!reviewer.trim()) {
      setError("Enter your name before reviewing a case.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const editedRecommendation =
        action === "edit"
          ? {
              decision: editedDecision,
              rationale: editedRationale,
              cited_guidelines: editedCitations
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            }
          : undefined;
      await reviewCase(caseId, { reviewer, action, notes, editedRecommendation });
      onClosed();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function submitChat() {
    if (!chatInput.trim()) return;
    const message = chatInput;
    setChatLog((log) => [...log, { from: "reviewer", text: message }]);
    setChatInput("");
    try {
      const response = await sendChatMessage(caseId, message);
      setChatLog((log) => [...log, { from: "assistant", text: response.reply }]);
    } catch (err) {
      setChatLog((log) => [...log, { from: "assistant", text: `Error: ${err.message}` }]);
    }
  }

  if (error && !caseData) return <p className="error">{error}</p>;
  if (!caseData) return <p>Loading case...</p>;

  const closed = TERMINAL_STATUSES.has(caseData.status);

  return (
    <div className="case-detail">
      <section className="case-summary-card">
        <h2>{caseData.case_id}</h2>
        <p>
          <strong>Status:</strong> {caseData.status}
        </p>
        <p>
          <strong>Member:</strong> {caseData.member?.name || "Unidentified"}{" "}
          {caseData.member?.member_id && `(${caseData.member.member_id})`}
        </p>
        <p>
          <strong>Procedure:</strong> {caseData.request?.procedure || "-"}
        </p>
        <p>
          <strong>Denial reference:</strong> {caseData.request?.denial_reference || "-"}
        </p>
      </section>

      <section className="case-summary-card">
        <h3>Clinical summary</h3>
        <p>{caseData.clinical_summary || "Not yet summarized."}</p>
      </section>

      <section className="case-summary-card">
        <h3>Recommendation</h3>
        {caseData.recommendation ? (
          <>
            <p>
              <strong>Decision:</strong> {caseData.recommendation.decision}
            </p>
            <p>{caseData.recommendation.rationale}</p>
            {caseData.recommendation.cited_guidelines?.length > 0 && (
              <ul>
                {caseData.recommendation.cited_guidelines.map((cite) => (
                  <li key={cite}>{cite}</li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <p>No recommendation drafted yet -- this case is awaiting clarification.</p>
        )}
      </section>

      {!closed && caseData.recommendation && (
        <section className="review-actions">
          <h3>Review</h3>
          {error && <p className="error">{error}</p>}
          <input
            type="text"
            placeholder="Your name"
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
          />
          <textarea
            placeholder="Notes (optional)"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />

          {editing && (
            <div className="edit-form">
              <label>
                Decision
                <input value={editedDecision} onChange={(e) => setEditedDecision(e.target.value)} />
              </label>
              <label>
                Rationale
                <textarea value={editedRationale} onChange={(e) => setEditedRationale(e.target.value)} />
              </label>
              <label>
                Cited guidelines (comma-separated)
                <input value={editedCitations} onChange={(e) => setEditedCitations(e.target.value)} />
              </label>
            </div>
          )}

          <div className="button-row">
            <button disabled={submitting} onClick={() => submitReview("approve")}>
              Approve
            </button>
            {!editing ? (
              <button disabled={submitting} onClick={() => setEditing(true)}>
                Edit
              </button>
            ) : (
              <button disabled={submitting} onClick={() => submitReview("edit")}>
                Save edit &amp; close
              </button>
            )}
            <button disabled={submitting} className="reject" onClick={() => submitReview("reject")}>
              Reject
            </button>
          </div>
        </section>
      )}

      <section className="case-summary-card">
        <h3>Audit trail</h3>
        <ul className="audit-list">
          {caseData.audit_trail.map((entry, i) => (
            <li key={i}>
              <span className="audit-time">{new Date(entry.timestamp).toLocaleString()}</span>
              {" - "}
              <strong>{entry.actor}</strong>: {entry.action}
            </li>
          ))}
        </ul>
      </section>

      <section className="chat-box">
        <h3>Chat (stub)</h3>
        <ul className="chat-log">
          {chatLog.map((entry, i) => (
            <li key={i} className={`chat-${entry.from}`}>
              {entry.text}
            </li>
          ))}
        </ul>
        <div className="button-row">
          <input
            type="text"
            placeholder="Ask about this case..."
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitChat()}
          />
          <button onClick={submitChat}>Send</button>
        </div>
      </section>
    </div>
  );
}
