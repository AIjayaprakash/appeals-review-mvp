import { useEffect, useState } from "react";
import { listCases } from "../api.js";

const STATUS_FILTERS = ["All", "Drafted", "Needs Clarification", "Reviewed", "Closed"];

export default function CaseQueue({ onSelectCase }) {
  const [status, setStatus] = useState("All");
  const [cases, setCases] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    setError(null);
    listCases(status === "All" ? undefined : status)
      .then(setCases)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [status]);

  return (
    <div className="queue">
      <div className="queue-filters">
        {STATUS_FILTERS.map((option) => (
          <button
            key={option}
            className={option === status ? "filter active" : "filter"}
            onClick={() => setStatus(option)}
          >
            {option}
          </button>
        ))}
      </div>

      {loading && <p>Loading cases...</p>}
      {error && <p className="error">{error}</p>}

      {!loading && !error && cases.length === 0 && <p>No cases in this queue.</p>}

      {!loading && !error && cases.length > 0 && (
        <table className="queue-table">
          <thead>
            <tr>
              <th>Case</th>
              <th>Member</th>
              <th>Status</th>
              <th>Recommendation</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((c) => (
              <tr key={c.case_id} onClick={() => onSelectCase(c.case_id)} className="queue-row">
                <td>{c.case_id}</td>
                <td>{c.member?.name || c.member?.member_id || "Unidentified"}</td>
                <td>
                  <span className={`status-badge status-${c.status.replace(/\s+/g, "-").toLowerCase()}`}>
                    {c.status}
                  </span>
                </td>
                <td>{c.recommendation?.decision || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
