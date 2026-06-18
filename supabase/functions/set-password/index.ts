// Supabase Edge Function: set-password
// Deploy as a function named "set-password".
// Lets a signed-in ADMIN create or reset another coordinator's email+password
// login. The service-role key stays here on the server, never in the browser.
//
// Deploy (easiest): Supabase dashboard -> Edge Functions -> Deploy a new
// function -> name it "set-password" -> paste this file -> Deploy.
// Or CLI: supabase functions deploy set-password
//
// SUPABASE_URL, SUPABASE_ANON_KEY, and SUPABASE_SERVICE_ROLE_KEY are provided
// to the function automatically by Supabase -- you don't set them.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...cors, "Content-Type": "application/json" },
  });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });

  try {
    const url = Deno.env.get("SUPABASE_URL")!;
    const anon = Deno.env.get("SUPABASE_ANON_KEY")!;
    const service = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
    const authHeader = req.headers.get("Authorization") ?? "";

    // 1) Verify the CALLER is an admin, using their own token.
    const caller = createClient(url, anon, {
      global: { headers: { Authorization: authHeader } },
    });
    const { data: isAdmin, error: adminErr } = await caller.rpc("is_admin");
    if (adminErr) throw adminErr;
    if (!isAdmin) return json({ error: "not authorized" }, 403);

    // 2) Validate input.
    const { email, password } = await req.json();
    if (!email || !password || String(password).length < 6) {
      return json({ error: "email and a 6+ character password are required" }, 400);
    }
    const target = String(email).toLowerCase();

    // 3) Create or update the auth user with the service-role client.
    const admin = createClient(url, service);
    const { data: list, error: listErr } =
      await admin.auth.admin.listUsers({ page: 1, perPage: 200 });
    if (listErr) throw listErr;
    const existing = list.users.find(
      (u) => (u.email ?? "").toLowerCase() === target,
    );

    if (existing) {
      const { error } = await admin.auth.admin.updateUserById(existing.id, {
        password,
        email_confirm: true,
      });
      if (error) throw error;
      return json({ ok: true, created: false });
    } else {
      const { error } = await admin.auth.admin.createUser({
        email: target,
        password,
        email_confirm: true,
      });
      if (error) throw error;
      return json({ ok: true, created: true });
    }
  } catch (e) {
    return json({ error: String((e as Error)?.message ?? e) }, 500);
  }
});
