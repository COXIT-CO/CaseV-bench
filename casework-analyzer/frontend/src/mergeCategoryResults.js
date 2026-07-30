// Combines the settled per-category /api/analyze responses from
// Multi-Prompting mode into one PageResult-shaped object per page, so the
// rest of the app (PageResultViewer.jsx, ResultsSummary.jsx, BBoxCanvas.jsx,
// PageGrid.jsx) can render it exactly like an ordinary single-prompt result
// without any changes of their own -- the only new piece those components
// need to understand is `category_errors` (see PageResultViewer.jsx).
//
// This mirrors the backend's parser.merge_page_results (used to keep the
// exports/session state correct across the same 4 independent requests) --
// they have to agree on what "merged" means, but stay separate because the
// frontend needs its own copy to render immediately without an extra round
// trip to re-fetch whatever the backend just merged.
export function mergeCategoryResponses(pageIds, categoryResults, pages) {
  const pageNumberById = new Map(pages.map((p) => [p.page_id, p.page_number]));
  const merged = {};

  for (const pageId of pageIds) {
    let anySuccess = false;
    let anyTruncated = false;
    const objects = [];
    const counts = {};
    const categoryErrors = {};
    const rawParts = [];
    let reasoningTokens = null;
    let completionTokens = null;

    for (const cr of categoryResults) {
      if (cr.status === "rejected") {
        categoryErrors[cr.category] = cr.reason?.message || "Request failed";
        continue;
      }
      const pageResult = cr.response.results.find((r) => r.page_id === pageId);
      if (!pageResult) continue;

      if (pageResult.status === "error") {
        categoryErrors[cr.category] = pageResult.error_message || "Request failed";
        continue;
      }

      anySuccess = true;
      if (pageResult.truncated) anyTruncated = true;

      if (pageResult.parsed) {
        objects.push(...pageResult.parsed.objects);
        for (const [key, value] of Object.entries(pageResult.parsed.counts || {})) {
          counts[key] = (counts[key] || 0) + value;
        }
      } else {
        categoryErrors[cr.category] = "Could not parse JSON from the response";
      }

      if (pageResult.raw_response) {
        rawParts.push(`--- ${cr.category} ---\n${pageResult.raw_response}`);
      }
      if (pageResult.reasoning_tokens != null) {
        reasoningTokens = (reasoningTokens || 0) + pageResult.reasoning_tokens;
      }
      if (pageResult.completion_tokens != null) {
        completionTokens = (completionTokens || 0) + pageResult.completion_tokens;
      }
    }

    merged[pageId] = {
      page_id: pageId,
      page_number: pageNumberById.get(pageId),
      status: anySuccess ? "done" : "error",
      parsed: anySuccess ? { objects, counts } : null,
      parse_error: !anySuccess,
      truncated: anyTruncated,
      error_message: anySuccess
        ? null
        : Object.entries(categoryErrors)
            .map(([c, m]) => `${c}: ${m}`)
            .join("; ") || "All category requests failed",
      raw_response: rawParts.length ? rawParts.join("\n\n") : null,
      reasoning_tokens: reasoningTokens,
      completion_tokens: completionTokens,
      category_errors: Object.keys(categoryErrors).length ? categoryErrors : null,
    };
  }

  return merged;
}
