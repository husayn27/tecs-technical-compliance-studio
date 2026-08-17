import { createClient } from "npm:@supabase/supabase-js@2.95.0";

const EXPECTED_TEAM_KEY_HASH = "424d9523595c1c60077f57f119cb7af75ce8c2d199457515905a2c3d1a45696b";
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function sha256(value: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function authorized(request: Request) {
  const supplied = request.headers.get("x-tecs-team-key") || "";
  return supplied.length >= 48 && await sha256(supplied) === EXPECTED_TEAM_KEY_HASH;
}

function serverSecret() {
  try {
    const configured = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}") as Record<string, string>;
    if (configured.default) return configured.default;
  } catch {
    // Fall through to the legacy environment variable while projects migrate.
  }
  return Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
}

Deno.serve(async (request) => {
  if (!await authorized(request)) return json({ detail: "Invalid TECS team workspace code." }, 401);

  const supabaseUrl = Deno.env.get("SUPABASE_URL") || "";
  const secret = serverSecret();
  if (!supabaseUrl || !secret) return json({ detail: "The project service is not configured." }, 503);
  const database = createClient(supabaseUrl, secret, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const url = new URL(request.url);
  const id = url.searchParams.get("id");

  if (request.method === "GET") {
    if (id) {
      const { data, error } = await database.from("tecs_team_projects").select("*").eq("id", id).maybeSingle();
      if (error) return json({ detail: error.message }, 500);
      if (!data) return json({ detail: "Project not found." }, 404);
      return json(data);
    }
    const { data, error } = await database
      .from("tecs_team_projects")
      .select("id,project_name,client,consultant,contractor,reference,status,progress,missing_fields,item_count,revision,created_at,updated_at,completed_at")
      .order("updated_at", { ascending: false })
      .limit(1000);
    return error ? json({ detail: error.message }, 500) : json(data || []);
  }

  if (request.method === "POST") {
    let body: Record<string, unknown>;
    try {
      body = await request.json();
    } catch {
      return json({ detail: "Invalid project payload." }, 400);
    }
    const projectName = String(body.project_name || "").trim();
    const draft = body.draft;
    if (!projectName || !draft || typeof draft !== "object") {
      return json({ detail: "Project name and draft are required." }, 400);
    }
    const status = body.status === "complete" ? "complete" : "pending";
    const payload = {
      project_name: projectName,
      client: String(body.client || "").trim(),
      consultant: String(body.consultant || "").trim(),
      contractor: String(body.contractor || "").trim(),
      reference: String(body.reference || "").trim(),
      status,
      progress: Math.max(0, Math.min(100, Number(body.progress) || 0)),
      missing_fields: Array.isArray(body.missing_fields) ? body.missing_fields.map(String).slice(0, 100) : [],
      item_count: Math.max(0, Number(body.item_count) || 0),
      draft,
      updated_at: new Date().toISOString(),
      completed_at: status === "complete" ? new Date().toISOString() : null,
    };

    if (!body.id) {
      const { data, error } = await database.from("tecs_team_projects").insert({ ...payload, revision: 1 }).select("*").single();
      return error ? json({ detail: error.message }, 500) : json(data, 201);
    }

    const expectedRevision = Number(body.expected_revision);
    if (!Number.isInteger(expectedRevision) || expectedRevision < 1) {
      return json({ detail: "A valid project revision is required." }, 400);
    }
    const { data, error } = await database
      .from("tecs_team_projects")
      .update({ ...payload, revision: expectedRevision + 1 })
      .eq("id", String(body.id))
      .eq("revision", expectedRevision)
      .select("*")
      .maybeSingle();
    if (error) return json({ detail: error.message }, 500);
    if (!data) return json({ detail: "This project was updated by another team member. Reload it before saving again." }, 409);
    return json(data);
  }

  if (request.method === "DELETE" && id) {
    const { error } = await database.from("tecs_team_projects").delete().eq("id", id);
    return error ? json({ detail: error.message }, 500) : json({ deleted: true });
  }

  return json({ detail: "Method not allowed." }, 405);
});
