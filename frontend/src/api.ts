export type ModelState = {
  state: string;
  progress: number;
  model_id?: string;
  name?: string;
  device?: string;
  error?: string | null;
  revision?: string;
};
export type Policy = { categories: string[]; custom_terms: string[] };
export type Box = { page: number; rect: [number, number, number, number] };
export type Finding = {
  id: string;
  label: string;
  text: string;
  source: string;
  score: number;
  boxes: Box[];
};
export type Document = {
  id: string;
  name: string;
  size: number;
  status: string;
  pages: number;
  finding_count: number;
  reviewed: boolean;
  error?: string;
  created_at: string;
  synthetic?: boolean;
  findings?: Finding[];
  page_sizes?: { width: number; height: number }[];
  excluded_finding_ids?: string[];
  manual_boxes?: Box[];
};
export type AppState = {
  documents: Document[];
  model: ModelState;
  session_id: string;
};
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      "X-Redact-Client": "veil",
      ...(options.body && !(options.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    let message = "The request could not be completed.";
    try {
      const data = await response.json();
      message =
        typeof data.detail === "string"
          ? data.detail
          : JSON.stringify(data.detail);
    } catch {
      /* non-JSON response */
    }
    throw new Error(message);
  }
  return response.json();
}
export async function download(path: string, name: string, body?: unknown) {
  const response = await fetch(`/api${path}`, {
    method: body ? "POST" : "GET",
    headers: {
      "X-Redact-Client": "veil",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const data = await response.json();
    throw new Error(
      data.detail || "Export failed. Review your document and try again.",
    );
  }
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
