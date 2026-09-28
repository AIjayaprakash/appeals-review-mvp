const JSON_HEADERS = { "Content-Type": "application/json" };

async function handle(response) {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json();
}

export function uploadCase(file) {
  const formData = new FormData();
  formData.append("file", file);
  return fetch("/intake/upload", { method: "POST", body: formData }).then(handle);
}

export function listCases(status) {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return fetch(`/cases${query}`).then(handle);
}

export function getCase(caseId) {
  return fetch(`/cases/${encodeURIComponent(caseId)}`).then(handle);
}

export function getAuditTrail(caseId) {
  return fetch(`/cases/${encodeURIComponent(caseId)}/audit`).then(handle);
}

export function reviewCase(caseId, { reviewer, action, notes, editedRecommendation }) {
  return fetch(`/cases/${encodeURIComponent(caseId)}/review`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({
      reviewer,
      action,
      notes: notes || null,
      edited_recommendation: editedRecommendation || null,
    }),
  }).then(handle);
}

export function sendChatMessage(caseId, message) {
  return fetch(`/cases/${encodeURIComponent(caseId)}/chat`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ message }),
  }).then(handle);
}
