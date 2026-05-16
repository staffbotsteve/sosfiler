-- Plan v2.6 §4.2.4 step 4 / PR7: Postgres evidence-invariant trigger.
--
-- The SQLite counterpart lives in backend/server.py init_db() as the
-- filing_jobs_evidence_invariant trigger. Both enforce the same predicate:
--
-- 1. orders.filing_confirmation must be a JSON object with a non-empty
--    `$.value` string. Raw strings, {}, {"value":""}, {"value":123} all fail.
-- 2. Submitted/submitted_to_state: requires submitted_receipt with sha256_hex.
-- 3. Approved/state_approved/documents_collected/complete: requires both a
--    submitted_receipt AND an action_type-appropriate terminal artifact
--    (formation=approved_certificate; annual_report adds state_correspondence,
--    state_acknowledgment, state_filed_document; amendment / foreign_qualification
--    / dissolution / reinstatement / certificate_of_good_standing add
--    state_filed_document; anything else conservatively requires approved_certificate).
--
-- The application + repository layers (PR1–PR6) already enforce this predicate
-- so legitimate flows never reach the trigger. The trigger is the last line of
-- defense against direct SQL writes and against any caller that bypasses the
-- repository.

create or replace function public.enforce_filing_evidence_invariant()
returns trigger
language plpgsql
as $$
declare
  conf_text text;
  conf_value text;
  terminal_types text[];
  has_submitted_receipt boolean;
  has_terminal boolean;
begin
  if new.status not in ('submitted','submitted_to_state','approved','state_approved',
                        'documents_collected','complete') then
    return new;
  end if;

  -- filing_confirmation precondition. Must be a JSON object whose $.value is
  -- a non-empty STRING. The value may be stored on execution_filing_jobs
  -- (mirror) or sourced from the orders row via legacy_job_id; here we
  -- consult new.filing_confirmation directly since the column was added in
  -- migration 20260516180000.
  conf_text := new.filing_confirmation;
  if conf_text is null or conf_text = '' then
    raise exception 'evidence_invariant: missing filing_confirmation' using errcode = '23514';
  end if;

  begin
    if jsonb_typeof(conf_text::jsonb -> 'value') <> 'string' then
      raise exception 'evidence_invariant: filing_confirmation $.value is not a string' using errcode = '23514';
    end if;
    conf_value := conf_text::jsonb ->> 'value';
  exception
    when invalid_text_representation then
      raise exception 'evidence_invariant: filing_confirmation is not valid JSON' using errcode = '23514';
    when others then
      raise exception 'evidence_invariant: malformed filing_confirmation' using errcode = '23514';
  end;

  if conf_value is null or length(conf_value) = 0 then
    raise exception 'evidence_invariant: filing_confirmation $.value is empty' using errcode = '23514';
  end if;

  -- Submitted-class transitions require a submitted_receipt artifact.
  if new.status in ('submitted','submitted_to_state') then
    if not exists (
      select 1 from public.execution_artifacts
      where filing_job_id = new.id
        and artifact_type = 'submitted_receipt'
        and is_evidence = true
        and sha256_hex is not null
    ) then
      raise exception 'evidence_invariant: missing submitted_receipt artifact' using errcode = '23514';
    end if;
    return new;
  end if;

  -- Approved-class transitions require both the receipt AND an action-aware
  -- terminal artifact.
  terminal_types := case
    when new.action_type = 'formation'      then array['approved_certificate']
    when new.action_type = 'annual_report'  then array['approved_certificate','state_correspondence',
                                                      'state_acknowledgment','state_filed_document']
    when new.action_type in ('amendment','foreign_qualification','dissolution',
                             'reinstatement','certificate_of_good_standing')
      then array['approved_certificate','state_filed_document']
    else array['approved_certificate']
  end;

  has_submitted_receipt := exists (
    select 1 from public.execution_artifacts
    where filing_job_id = new.id
      and artifact_type = 'submitted_receipt'
      and is_evidence = true
      and sha256_hex is not null
  );
  has_terminal := exists (
    select 1 from public.execution_artifacts
    where filing_job_id = new.id
      and artifact_type = any(terminal_types)
      and is_evidence = true
      and sha256_hex is not null
  );

  if not has_submitted_receipt or not has_terminal then
    raise exception 'evidence_invariant: approved status requires submitted_receipt and an action_type-appropriate terminal artifact'
      using errcode = '23514';
  end if;

  return new;
end;
$$;

-- Plan v2.6 §4.2.4 step 4 / PR7 codex round-1 P2: fire on both INSERT and
-- UPDATE so first-time mirror/backfill writes that land directly in a
-- terminal status cannot bypass the predicate.
drop trigger if exists trg_execution_filing_jobs_evidence on public.execution_filing_jobs;
create trigger trg_execution_filing_jobs_evidence
  before insert or update on public.execution_filing_jobs
  for each row execute function public.enforce_filing_evidence_invariant();
