import { pageImageUrl } from "../api.js";

export default function ImagePreviewModal({ page, onClose }) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-content"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <h3>Page {page.page_number}</h3>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <img
          src={pageImageUrl(page.image_url)}
          alt={`Page ${page.page_number}`}
        />
      </div>
    </div>
  );
}
