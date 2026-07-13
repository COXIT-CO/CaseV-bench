import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/api";
import { META } from "@/test/fixtures";

function mockFetch(response: Partial<Response> & { json: () => Promise<unknown> }) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("meta() fetches /api/meta and returns the parsed body", async () => {
    mockFetch({ ok: true, json: () => Promise.resolve(META) });

    await expect(api.meta()).resolves.toEqual(META);
    expect(fetch).toHaveBeenCalledWith(
      "/api/meta",
      expect.objectContaining({ headers: { Accept: "application/json" } }),
    );
  });

  it("throws ApiError carrying the {detail} envelope on a non-2xx response", async () => {
    mockFetch({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: () => Promise.resolve({ detail: "Result not found" }),
    });

    await expect(api.meta()).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
      detail: "Result not found",
    });
  });

  it("wraps a network failure in an ApiError with status 0", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("boom")));

    const err = await api.meta().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
  });
});
