# openoutreach/core/management/commands/prompt_ab.py
"""Side-by-side qualification-prompt comparison on a real lead sample.

Runs two prompt variants (strict vs loose) through the same LLM call path on the
same profiles and prints the verdicts next to each other, so an operator can see
what loosening actually changes — and re-judge the existing rejection pool to
measure how many old ``wrong_fit`` verdicts the new prompt would rescue.

Plain A/B mode is read-only. ``--apply`` (rejects source only) re-runs the loose
prompt against the campaign's ``FAILED``+``wrong_fit`` deals and flips the ones
that now qualify back to ``QUALIFIED`` — the same state change the Reviews page
performs by hand, en masse: the deal rejoins the rank gate, and the GP's next
refit relabels it a fit positive (its label derivation reads state), so the model
unlearns the mistaken rejection. The original rejection reason stays on the deal
as the audit trail. Requires an explicit ``--yes`` (or a typed confirmation).

    python manage.py prompt_ab --campaign 1 --sample 20 --source rejects
    python manage.py prompt_ab --source pool --sample 10 --dry-run
    python manage.py prompt_ab --source rejects --sample 30 --csv ab-report.csv
    python manage.py prompt_ab --source rejects --apply --yes --csv rescue.csv
"""
from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field

from django.core.management.base import BaseCommand, CommandError
from termcolor import colored

from openoutreach.crm.models import DealState, Outcome

DEFAULT_STRICT = "qualify_lead_strict.j2"   # the pre-loosening prompt
DEFAULT_LOOSE = "qualify_lead.j2"           # the current borderline-aware prompt


@dataclass
class Verdict:
    label: int | None = None   # 1 accept, 0 reject, None = call failed
    reason: str = ""
    error: str = ""


@dataclass
class Row:
    lead_id: int
    identity: str              # email if known, else profile URL
    profile_text: str = ""
    strict: Verdict = field(default_factory=Verdict)
    loose: Verdict = field(default_factory=Verdict)
    original_reason: str = ""  # rejects source only — the stored wrong_fit reason
    deal_id: int | None = None  # rejects source only — for the --apply flip


class Command(BaseCommand):
    help = "Run strict vs loose qualification prompts side by side on a lead sample (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--campaign", type=int, help="Campaign pk (default: first non-freemium).")
        parser.add_argument("--source", choices=("pool", "rejects"), default="pool",
                            help="pool: un-dealt leads awaiting qualification. "
                                 "rejects: the campaign's FAILED wrong_fit deals, re-judged.")
        parser.add_argument("--sample", type=int, default=20,
                            help="How many oldest-first leads to judge (default: 20).")
        parser.add_argument("--strict", default=DEFAULT_STRICT,
                            help=f"Strict template in PROMPTS_DIR (default: {DEFAULT_STRICT}).")
        parser.add_argument("--apply", action="store_true",
                            help="REQUIRES --source rejects: re-judge with the loose prompt and "
                                 "flip now-accepted deals back to QUALIFIED. Writes to the DB.")
        parser.add_argument("--yes", action="store_true",
                            help="Skip the confirmation prompt (for --apply).")
        parser.add_argument("--loose", default=DEFAULT_LOOSE,
                            help=f"Loose template in PROMPTS_DIR (default: {DEFAULT_LOOSE}).")
        parser.add_argument("--delay", type=float, default=0.5,
                            help="Seconds between LLM calls (default: 0.5).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Print the sample and both rendered prompts; call no LLM.")
        parser.add_argument("--csv", default="",
                            help="Write the per-lead verdicts to this CSV path.")

    def handle(self, *args, **options):
        from openoutreach.core.conf import PROMPTS_DIR
        from openoutreach.core.models import Campaign
        from openoutreach.crm.models import Deal, Lead
        from openoutreach.core.ml.qualifier import qualify_with_llm

        campaign = self._resolve_campaign(options["campaign"], Campaign)
        source = options["source"]
        n = max(1, options["sample"])
        strict_t, loose_t = options["strict"], options["loose"]

        rows = self._sample(source, campaign, n, Deal, Lead)
        if not rows:
            self.stdout.write(self.style.WARNING(
                f"No leads to judge (source={source}, campaign={campaign.name})."))
            return

        if options["dry_run"]:
            self._dry_run(rows, campaign, strict_t, loose_t, PROMPTS_DIR)
            return

        if options["apply"]:
            self._run_apply(rows, campaign, loose_t, options)
            return

        self.stdout.write(colored(
            f"judging {len(rows)} lead(s) with '{strict_t}' vs '{loose_t}' "
            f"({campaign.name})", attrs=["bold"]))
        for i, row in enumerate(rows, 1):
            if not row.profile_text.strip():
                row.strict.error = row.loose.error = "no profile text — skipped"
                self.stdout.write(f"[{i}/{len(rows)}] {row.identity} — skipped (no profile text)")
                continue
            row.strict = self._call(qualify_with_llm, campaign, row.profile_text, strict_t)
            time.sleep(options["delay"])
            row.loose = self._call(qualify_with_llm, campaign, row.profile_text, loose_t)
            time.sleep(options["delay"])
            self.stdout.write(f"[{i}/{len(rows)}] {row.identity} — judged")

        self._print_table(rows, source)
        summary = self._summarize(rows)
        self._print_summary(summary, source)
        if options["csv"]:
            self._write_csv(rows, source, options["csv"])

    # -- Steps ---------------------------------------------------------------

    def _resolve_campaign(self, pk, Campaign):
        if pk:
            return Campaign.objects.get(pk=pk)
        c = Campaign.objects.filter(is_freemium=False).order_by("pk").first()
        if not c:
            raise CommandError("No campaign — pass --campaign explicitly.")
        return c

    def _sample(self, source, campaign, n, Deal, Lead):
        """Oldest-first deterministic sample, mirroring the pipeline's pools."""
        if source == "pool":
            qs = (
                Lead.objects.filter(disqualified=False, embedding__isnull=False)
                .exclude(deal__campaign=campaign)
                .order_by("creation_date")
            )
            rows = [Row(lead_id=l.pk, identity=l.email or l.profile_url,
                        profile_text=l.profile_text or "") for l in qs[:n]]
        else:
            qs = (
                Deal.objects.filter(campaign=campaign, state=DealState.FAILED,
                                    outcome=Outcome.WRONG_FIT)
                .select_related("lead")
                .order_by("creation_date")
            )
            rows = [Row(lead_id=d.lead_id,
                        identity=d.lead.email or d.lead.profile_url,
                        profile_text=d.lead.profile_text or "",
                        original_reason=d.reason or "",
                        deal_id=d.pk) for d in qs[:n]]
        return rows

    def _call(self, qualify_with_llm, campaign, profile_text, template):
        try:
            label, reason = qualify_with_llm(
                profile_text,
                product_docs=campaign.product_docs,
                campaign_target=campaign.campaign_target,
                template=template,
            )
            return Verdict(label=label, reason=reason)
        except Exception as e:  # one bad call must not kill the whole run
            return Verdict(label=None, error=f"{type(e).__name__}: {e}")

    def _dry_run(self, rows, campaign, strict_t, loose_t, PROMPTS_DIR):
        import jinja2

        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)))
        self.stdout.write(colored(f"DRY RUN — {len(rows)} lead(s), no LLM calls.\n", attrs=["bold"]))
        for row in rows:
            self.stdout.write(f"  {row.identity}  (lead {row.lead_id})"
                              + (f"  · originally rejected: {row.original_reason[:80]}" if row.original_reason else ""))
        self.stdout.write("\n" + colored("Strict template renders as:", attrs=["bold"]))
        self.stdout.write(env.get_template(strict_t).render(
            product_docs=campaign.product_docs, campaign_target=campaign.campaign_target,
            profile_text=rows[0].profile_text))
        self.stdout.write("\n" + colored("Loose template renders as:", attrs=["bold"]))
        self.stdout.write(env.get_template(loose_t).render(
            product_docs=campaign.product_docs, campaign_target=campaign.campaign_target,
            profile_text=rows[0].profile_text))

    # -- Reporting -----------------------------------------------------------

    def _print_table(self, rows, source):
        self.stdout.write("")
        for i, row in enumerate(rows, 1):
            agree = row.strict.label is not None and row.strict.label == row.loose.label
            flag = " " if agree else colored("≠", "yellow", attrs=["bold"])
            s = self._verdict_cell(row.strict)
            l = self._verdict_cell(row.loose)
            orig = f"  [was: {row.original_reason[:60]}]" if source == "rejects" and row.original_reason else ""
            self.stdout.write(
                f"{flag} {i:>3} {row.identity[:40]:<40} strict {s:<14} loose {l:<14}{orig}")
            if row.strict.label is not None and row.loose.label is not None \
                    and row.strict.label != row.loose.label:
                self.stdout.write(f"     strict said: {row.strict.reason[:90]}")
                self.stdout.write(f"     loose  said: {row.loose.reason[:90]}")

    @staticmethod
    def _verdict_cell(v: Verdict) -> str:
        if v.label is None:
            return colored("ERR", "red")
        if v.label == 1:
            return colored("✓ accept", "green")
        return colored("✗ reject", "red")

    def _summarize(self, rows):
        judged = [r for r in rows if r.strict.label is not None and r.loose.label is not None]
        both = sum(1 for r in judged if r.strict.label == 1 and r.loose.label == 1)
        neither = sum(1 for r in judged if r.strict.label == 0 and r.loose.label == 0)
        strict_only = sum(1 for r in judged if r.strict.label == 1 and r.loose.label == 0)
        loose_only = sum(1 for r in judged if r.strict.label == 0 and r.loose.label == 1)
        return {"judged": len(judged), "both": both, "neither": neither,
                "strict_only": strict_only, "loose_only": loose_only}

    # -- Apply (requalify) ------------------------------------------------

    def _run_apply(self, rows, campaign, loose_t, options):
        from openoutreach.crm.models import Deal
        from openoutreach.core.db.deals import requalify_deal
        from openoutreach.core.ml.qualifier import qualify_with_llm

        source = options["source"]
        if source != "rejects":
            raise CommandError("--apply only makes sense against the rejection pool; "
                               "pass --source rejects.")
        if not options["yes"]:
            self.stdout.write(colored(
                f"This will re-judge {len(rows)} rejected lead(s) with '{loose_t}' "
                f"({campaign.name}) and FLIP the accepted ones back to QUALIFIED — "
                f"a real DB write ({1} LLM call per lead).", attrs=["bold"]))
            answer = input("Type 'yes' to continue: ").strip().lower()
            if answer != "yes":
                self.stdout.write(self.style.WARNING("Aborted — nothing was changed."))
                return

        self.stdout.write(colored(
            f"re-judging {len(rows)} rejected lead(s) with '{loose_t}' ({campaign.name})",
            attrs=["bold"]))
        requalified = still_rejected = errors = skipped = 0
        for i, row in enumerate(rows, 1):
            if not row.profile_text.strip():
                skipped += 1
                self.stdout.write(f"[{i}/{len(rows)}] {row.identity} — skipped (no profile text)")
                continue
            v = self._call(qualify_with_llm, campaign, row.profile_text, loose_t)
            time.sleep(options["delay"])
            if v.label is None:
                errors += 1
                self.stdout.write(f"[{i}/{len(rows)}] {row.identity} — {colored('ERR', 'red')} {v.error}")
                continue
            if v.label == 1:
                deal = Deal.objects.get(pk=row.deal_id)
                requalify_deal(deal, user=None, source="prompt_ab")
                requalified += 1
                self.stdout.write(f"[{i}/{len(rows)}] {colored('✓ requalified', 'green'):<14} "
                                  f"{row.identity[:42]:<42} {v.reason[:70]}")
            else:
                still_rejected += 1
                self.stdout.write(f"[{i}/{len(rows)}] {colored('✗ still rejected', 'red'):<14} "
                                  f"{row.identity[:42]:<42} {v.reason[:70]}")
            row.loose = v

        self.stdout.write(colored(f"\n── apply summary ──", attrs=["bold"]))
        self.stdout.write(f"  judged:          {requalified + still_rejected + errors}")
        self.stdout.write(colored(f"  requalified:     {requalified}", "green"))
        self.stdout.write(colored(f"  still rejected:  {still_rejected}", "red"))
        self.stdout.write(f"  errors:          {errors}")
        self.stdout.write(f"  skipped:         {skipped}")
        self.stdout.write(colored(
            f"\n  → {requalified} lead(s) are back in the qualified pool — the next rank-gate "
            f"pass scores them and the GP refit relabels them fit positives.", attrs=["bold"]))
        if options["csv"]:
            self._write_apply_csv(rows, options["csv"])

    def _write_apply_csv(self, rows, path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["lead_id", "identity", "action", "loose_reason", "original_reason"])
            for r in rows:
                action = ("requalified" if r.loose.label == 1
                          else "still_rejected" if r.loose.label == 0
                          else f"error: {r.loose.error}" if r.loose.error
                          else "skipped")
                w.writerow([r.lead_id, r.identity, action, r.loose.reason, r.original_reason])
        self.stdout.write(colored(f"\nwrote {path}", attrs=["bold"]))

    def _print_summary(self, s, source):
        n = s["judged"]
        self.stdout.write(colored("\n── summary ──", attrs=["bold"]))
        if n == 0:
            self.stdout.write(self.style.WARNING("  nothing judged (LLM calls all failed?)."))
            return
        pct = lambda k: f"{100 * s[k] / n:.0f}%"
        self.stdout.write(f"  judged:          {n}")
        self.stdout.write(f"  both accept:     {s['both']}  ({pct('both')})")
        self.stdout.write(f"  both reject:     {s['neither']}  ({pct('neither')})")
        self.stdout.write(colored(f"  strict-only:     {s['strict_only']}  ({pct('strict_only')})  — strict accepts, loose rejects", "red"))
        self.stdout.write(colored(f"  loose-only:      {s['loose_only']}  ({pct('loose_only')})  — loose rescues", "green"))
        disagree = s["strict_only"] + s["loose_only"]
        self.stdout.write(f"  disagree:        {disagree}  ({100 * disagree / n:.0f}%)")
        if source == "rejects":
            self.stdout.write(colored(
                f"\n  → {s['loose_only']} of {n} previously-rejected lead(s) "
                f"({pct('loose_only')}) now qualify under the loose prompt.", attrs=["bold"]))

    def _write_csv(self, rows, source, path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["lead_id", "identity", "strict_verdict", "strict_reason",
                        "loose_verdict", "loose_reason",
                        *(["original_reason"] if source == "rejects" else [])])
            for r in rows:
                w.writerow([
                    r.lead_id, r.identity,
                    "accept" if r.strict.label == 1 else ("reject" if r.strict.label == 0 else f"error: {r.strict.error}"),
                    r.strict.reason,
                    "accept" if r.loose.label == 1 else ("reject" if r.loose.label == 0 else f"error: {r.loose.error}"),
                    r.loose.reason,
                    *([r.original_reason] if source == "rejects" else []),
                ])
        self.stdout.write(colored(f"\nwrote {path}", attrs=["bold"]))
