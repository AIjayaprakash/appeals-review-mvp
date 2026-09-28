import { useRef, useState } from "react";
import { uploadCase } from "../api.js";

const ACCEPTED_EXTENSIONS = ".eml,.pdf";

export default function UploadCase({ onUploaded }) {
  const [file, setFile] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  async function submit() {
    if (!file) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await uploadCase(file);
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      onUploaded(created);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="upload-card">
      <h3>Submit a new appeal</h3>
      <p className="upload-hint">Browse for an appeal email (.eml) or a clinical document (.pdf) to run through intake.</p>
      <div className="button-row">
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED_EXTENSIONS}
          onChange={(e) => setFile(e.target.files[0] || null)}
          disabled={submitting}
        />
        <button disabled={!file || submitting} onClick={submit}>
          {submitting ? "Processing..." : "Upload & process"}
        </button>
      </div>
      {submitting && <p className="upload-status">Running intake, evaluation, and summarization -- this can take a few seconds.</p>}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
