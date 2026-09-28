import { useState } from "react";
import CaseQueue from "./components/CaseQueue.jsx";
import CaseDetail from "./components/CaseDetail.jsx";

export default function App() {
  const [selectedCaseId, setSelectedCaseId] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <div className="app">
      <header className="app-header">
        <h1>Appeals &amp; Grievances Review Copilot</h1>
        {selectedCaseId && (
          <button className="link-button" onClick={() => setSelectedCaseId(null)}>
            &larr; Back to queue
          </button>
        )}
      </header>

      {selectedCaseId ? (
        <CaseDetail
          caseId={selectedCaseId}
          onClosed={() => {
            setSelectedCaseId(null);
            setRefreshKey((k) => k + 1);
          }}
        />
      ) : (
        <CaseQueue key={refreshKey} onSelectCase={setSelectedCaseId} />
      )}
    </div>
  );
}
