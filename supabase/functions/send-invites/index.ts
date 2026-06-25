// Supabase Edge Function: send-invites
// Deploy as a function named "send-invites".
//
// The worker half of the old send_invites.py. It asks the database which
// remote sign-in invites are due (invites_due), emails each one through Gmail
// (denomailer over SMTP SSL, the same Gmail app password the script used),
// marks each invite sent only AFTER it goes out, and logs every attempt to
// email_log. Driven on a 10-minute schedule by Supabase Cron (cron_invites.sql),
// which replaces the GitHub "Send invites" workflow.
//
// Deploy (easiest): Supabase dashboard -> Edge Functions -> Deploy a new
// function -> name it "send-invites" -> paste this file -> Deploy.
// Or CLI: supabase functions deploy send-invites
//
// SUPABASE_URL / SUPABASE_ANON_KEY / SUPABASE_SERVICE_ROLE_KEY are injected by
// Supabase automatically. You DO set these three as function secrets
// (Edge Functions -> send-invites -> Secrets):
//   GMAIL_USER           e.g. frontenaczonecsp@gmail.com
//   GMAIL_APP_PASSWORD   the 16-char Gmail app password
//   SITE_URL             https://dunleavaa.github.io/patrol   (no trailing slash)
//   FROM_NAME            (optional) overrides the app_settings from-name
//
// Request body (all optional): { mode, shift, lead, grace, dry_run }
//   mode:    "day_of" (default) | "window"
//   shift:   force one shift id now, ignoring timing (testing)
//   lead:    minutes before start to send in window mode (default 15)
//   grace:   also catch starts up to N min ago in window mode (default 30)
//   dry_run: true -> list who would be emailed; send nothing, mint nothing

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { SMTPClient } from "https://deno.land/x/denomailer@1.6.0/mod.ts";

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

// Substitute {placeholder} tokens; leaves unknown braces untouched. (= fill())
function fill(template: string, repl: Record<string, string>): string {
  let out = template ?? "";
  for (const [k, v] of Object.entries(repl)) {
    out = out.split("{" + k + "}").join(v ?? "");
  }
  return out;
}

// Fallbacks used only if the matching app_settings row is blank/missing.
const DEFAULT_SUBJECT = "Your patrol event starts soon — sign in";
const DEFAULT_TEXT =
  "Hi {first_name},\n\nYour Special Events shift is coming up:\n  {event}\n  {when}\n\n" +
  "Tap to sign in (no password needed):\n{link}\n\n" +
  "You don't need to sign out — your scheduled hours are recorded when you confirm.\n\n— {from_name}\n";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });

  try {
    const url = Deno.env.get("SUPABASE_URL")!;
    const anon = Deno.env.get("SUPABASE_ANON_KEY")!;
    const service = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
    const authHeader = req.headers.get("Authorization") ?? "";
    const bearer = authHeader.replace(/^Bearer\s+/i, "").trim();

    // Authorize the caller. Two trusted paths:
    //   1) Supabase Cron / system: presents the service-role key as the bearer.
    //   2) A signed-in admin (e.g. a manual "send now" from the dashboard).
    const isSystem = bearer.length > 0 && bearer === service;
    if (!isSystem) {
      const caller = createClient(url, anon, {
        global: { headers: { Authorization: authHeader } },
      });
      const { data: isAdmin, error: adminErr } = await caller.rpc("is_admin");
      if (adminErr) throw adminErr;
      if (!isAdmin) return json({ error: "not authorized" }, 403);
    }

    const body = await req.json().catch(() => ({}));
    const mode: string = body.mode ?? "day_of";
    const shift: string | null = body.shift ?? null;
    const lead: number = Number.isFinite(body.lead) ? body.lead : 15;
    const grace: number = Number.isFinite(body.grace) ? body.grace : 30;
    const dryRun: boolean = body.dry_run === true;

    const site = (Deno.env.get("SITE_URL") ?? "").replace(/\/+$/, "");
    const gUser = Deno.env.get("GMAIL_USER") ?? "";
    const gPass = Deno.env.get("GMAIL_APP_PASSWORD") ?? "";

    if (!dryRun && (!gUser || !gPass)) {
      return json({ error: "GMAIL_USER and GMAIL_APP_PASSWORD must be set" }, 500);
    }
    if (!dryRun && !site) {
      return json({ error: "SITE_URL must be set so links resolve" }, 500);
    }

    const admin = createClient(url, service);

    // Editable templates / from-identity live in app_settings.
    const cfg: Record<string, string> = {};
    {
      const { data, error } = await admin.from("app_settings").select("key,value");
      if (error) throw error;
      for (const row of data ?? []) cfg[row.key] = row.value ?? "";
    }
    const fromName = Deno.env.get("FROM_NAME") || cfg["email_from_name"] || "Frontenac Zone Ski Patrol";
    const fromAddr = cfg["email_from_address"] || gUser;
    const replyTo = cfg["email_reply_to"] || "";
    const tSubject = cfg["email_subject"] || DEFAULT_SUBJECT;
    const tText = cfg["email_body_text"] || DEFAULT_TEXT;
    const tHtml = cfg["email_body_html"] || ""; // optional; text always present

    // Ask the DB for the due list (mint tokens unless this is a dry run).
    const { data: rows, error: dueErr } = await admin.rpc("invites_due", {
      p_mode: mode,
      p_shift: shift,
      p_lead: lead,
      p_grace: grace,
      p_mint: !dryRun,
    });
    if (dueErr) throw dueErr;

    if (!rows || rows.length === 0) {
      return json({ ok: true, dry_run: dryRun, candidates: 0, sent: 0, skipped: 0 });
    }

    if (dryRun) {
      const preview = rows.map((r: any) => ({
        to: r.to_email, name: r.to_name, event: r.event_name, when: r.when_text,
      }));
      return json({ ok: true, dry_run: true, candidates: rows.length, preview });
    }

    // One SMTP connection reused for the whole batch.
    const client = new SMTPClient({
      connection: {
        hostname: "smtp.gmail.com",
        port: 465,
        tls: true,
        auth: { username: gUser, password: gPass },
      },
    });

    let sent = 0, skipped = 0;
    try {
      for (const r of rows as any[]) {
        if (!r.to_email || !r.token) { skipped++; continue; }

        const link = `${site}/signin.html?t=${r.token}`;
        const repl: Record<string, string> = {
          first_name: r.first_name || "",
          name: r.to_name || "",
          event: r.event_name || "Special Event",
          when: r.when_text || "",
          link,
          from_name: fromName,
        };
        const subject = fill(tSubject, repl);
        const text = fill(tText, repl);
        const html = tHtml ? fill(tHtml, repl) : undefined;

        try {
          await client.send({
            from: `${fromName} <${fromAddr}>`,
            to: r.to_name ? `${r.to_name} <${r.to_email}>` : r.to_email,
            replyTo: replyTo || undefined,
            subject,
            content: text,
            html,
          });
        } catch (e) {
          await admin.from("email_log").insert({
            to_email: r.to_email, subject, shift_id: r.shift_id,
            kind: "invite", status: "failed", error: String((e as Error)?.message ?? e).slice(0, 500),
          });
          skipped++;
          continue;
        }

        // Mark sent ONLY after a successful send, so failures retry next run.
        await admin.from("remote_invites").update({ sent_at: new Date().toISOString() })
          .eq("token", r.token);
        await admin.from("email_log").insert({
          to_email: r.to_email, subject, shift_id: r.shift_id,
          kind: "invite", status: "sent",
        });
        sent++;
      }
    } finally {
      try { await client.close(); } catch (_) { /* ignore */ }
    }

    return json({ ok: true, dry_run: false, candidates: rows.length, sent, skipped });
  } catch (e) {
    return json({ error: String((e as Error)?.message ?? e) }, 500);
  }
});
