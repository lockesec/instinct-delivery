# Invariants and the tests that pin them

Each behaviour the README claims, with the test that fails if it breaks.

## Loading

| Rule | Test |
|---|---|
| a rule with a null or blank `action` is rejected, never loaded to fail later | `test_rules_and_match.py::test_an_unusable_rule_is_rejected_with_its_reason` |
| an unknown (misspelled) status is rejected, never fired as active | same |
| a `match_content` pattern that does not compile is rejected, never degraded to a substring | same |
| no `match_tools` → rejected; an empty-string entry → rejected | same |
| duplicate keys and non-mapping documents are rejected | `test_duplicate_keys_and_non_mappings_are_rejected` |
| status is case/space/comment-insensitive; blank = active | `test_status_vocabulary_is_closed_and_normalised` |
| active/candidate/retired go to fire/shadow/none | `test_pools_by_status` |
| only `*.yaml` directly in the dir, in file-name order | `test_only_yaml_directly_in_the_dir_is_read` |
| `doctor` names every rejection and every over-budget rule; nothing loaded is NOT EVALUABLE | `test_hook_and_cli.py::test_doctor_*` |
| a `confidence` that is not a finite number (a date, a set, NaN, a bool) is rejected, and never silences a neighbour | `test_hardening.py::test_a_confidence_that_is_not_a_finite_number_is_rejected`, `test_a_rejected_confidence_never_silences_its_neighbour` |
| a pattern that nests an unbounded quantifier in a repeat is rejected at load and fails `doctor`; ordinary patterns load | `test_a_catastrophic_pattern_is_rejected_at_load`, `test_doctor_fails_on_a_catastrophic_pattern`, `test_ordinary_patterns_still_load` |
| a `*.yml` file is named by `doctor` as not read, exit 1 | `test_a_yml_file_is_named_by_doctor` |

## Matching

| Rule | Test |
|---|---|
| tool filter is exact and case-sensitive | `test_tool_filter_is_exact_and_case_sensitive` |
| **negative control:** a call matching no rule emits nothing and logs nothing | `test_no_match_returns_nothing`, `test_engine.py::test_no_match_emits_nothing_and_logs_nothing` |
| **negative control:** an exclusion silences the rule before any positive filter | `test_exclude_silences_before_any_positive_filter`, `test_an_excluded_path_is_silent` |
| paths are case-sensitive substrings; content is a case-insensitive regex | `test_paths_substring_case_sensitive_content_regex_case_insensitive` |
| `match_commands` only matches Bash | `test_commands_only_match_bash` |
| kinds AND, entries OR | `test_kinds_and_together_entries_or_together` |
| evidence is the rule's declared token, never the call's text | `test_evidence_is_the_declared_token_never_the_input` |
| notebook paths, search roots and multi-edit paths are all matched | `test_every_declared_path_field_is_matched` |
| malformed input (null, non-dict, non-string fields) is read as empty, never raised on | `test_malformed_input_is_read_as_empty_never_raised_on` |
| Bash command text is searched by content rules | `test_bash_command_text_is_searched_by_content_rules` |

## Writes made through Bash

| Rule | Test |
|---|---|
| heredoc / tee / redirect / `sed -i` / echo / printf / Python heredoc writes are found, with the written text as content | `test_bashwrites.py::test_every_visible_write_shape_is_found` |
| a quoted `>`, a sink, a `$VAR` target, a reading-only Python body, a non-in-place sed claim nothing | `test_no_write_is_claimed_where_none_is_visible` |
| a Write-scoped rule fires on a Bash write, tagged `via=bash-write`; off → nothing | `test_a_write_scoped_rule_fires_on_a_bash_write_with_via` |
| an Edit-scoped rule fires on a Bash write (`sed -i`, redirect) as an edit, once; a rule scoped to neither does not; breadth counts the write as eligible for it | `test_bash_write_matching.py` |
| a rule hit by the command itself is not delivered twice | `test_a_rule_hit_by_the_command_is_not_delivered_twice` |
| a quoted target is found; `<<` in arithmetic is not a heredoc; a heredoc with no terminator runs to the end, as in bash | `test_hardening.py::test_a_quoted_write_target_is_found`, `test_arithmetic_shift_is_not_a_heredoc`, `test_a_heredoc_without_its_terminator_runs_to_the_end_as_in_bash` |
| an exclusion anywhere in the command silences a derived write hit | `test_an_exclusion_in_the_command_silences_a_derived_write_hit` |

## Delivery

| Rule | Test |
|---|---|
| first fire full, repeat brief with its count and without the body | `test_engine.py::test_first_fire_full_repeat_brief_with_count` |
| the re-arm window restores the full text; `0` never re-arms | `test_the_rearm_window_restores_full_text` |
| `REPEAT=full` is byte-identical across fires and writes no state | `test_repeat_off_is_byte_identical_across_fires` |
| no session id → no suppression, no state, `session_id: null` | `test_no_session_id_means_no_suppression_and_no_state` |
| sessions do not share state | `test_sessions_do_not_share_state` |
| past the brief cap → muted; a full delivery reopens it | `test_brief_cap_mutes_then_rearm_reopens` |
| damaged state fails toward full delivery, including a wrong-typed counter and a future timestamp | `test_damaged_state_fails_toward_full_delivery`, `test_hardening.py::test_a_corrupt_counter_fails_toward_delivery`, `test_a_future_timestamp_rearms_instead_of_muting` |
| an unwritable state dir never blocks delivery | `test_an_unwritable_state_dir_never_blocks_delivery` |
| a session id cannot escape the state dir, and two distinct ids never share a state file | `test_a_session_id_cannot_escape_the_state_dir`, `test_hardening.py::test_distinct_session_ids_never_share_state` |
| a typo in a setting keeps the default, never 0 | `test_a_typo_in_a_knob_keeps_the_default_never_zero` |
| **the budget holds every emitted byte**, including at every boundary, with a full closing line, with a huge domain, at tiny budgets, and counted as JSON-escaped bytes | `test_the_budget_holds_every_emitted_byte`, `test_the_budget_holds_at_every_boundary`, `test_the_budget_holds_when_the_withheld_line_is_full`, `test_hardening.py::test_a_long_domain_cannot_overflow_the_default_budget`, `test_a_small_budget_holds`, `test_the_budget_holds_over_a_sweep_of_fields_and_budgets`, `test_the_budget_counts_the_json_escaped_bytes` |
| the byte counter equals the bytes emitted, the withheld notice included | `test_the_byte_counter_equals_the_bytes_emitted`, `test_hardening.py::test_the_withheld_notice_is_in_the_telemetry` |
| under budget the strongest rule is kept | `test_under_budget_the_strongest_rule_is_kept` |
| **negative control:** a withheld rule is never marked delivered; its next fire is full | `test_a_withheld_rule_is_delivered_in_full_on_its_next_fire` |
| a rule bigger than the budget is cut to fit, alone or behind a stronger co-firing rule; with a session a withheld rule goes first next time; without one a crowded rule is cut to fit | `test_a_rule_bigger_than_the_whole_budget_is_truncated_not_starved`, `test_hardening.py::test_an_oversized_rule_behind_a_stronger_one_is_not_starved`, `test_a_withheld_rule_goes_first_on_its_next_fire`, `test_without_a_session_a_crowded_rule_is_cut_to_fit_not_withheld_forever` |
| budget 0 is byte-identical to no budget | `test_budget_zero_is_byte_identical_to_unbudgeted` |
| **negative control:** a retired rule never fires and never shadows | `test_a_retired_rule_never_fires_and_never_shadows` |
| shadow is byte-identical to no shadow, is logged, and never touches delivery state | `test_shadow_is_byte_identical_and_never_touches_state` |
| one failing rule never silences another, and the others' state is still saved | `test_one_failing_rule_never_silences_another` |
| rows of one call share one timestamp and carry the schema in order | `test_rows_of_one_call_share_one_timestamp_and_carry_the_schema` |

## Adapter and CLI

| Rule | Test |
|---|---|
| a match emits exactly the PreToolUse context envelope, exit 0 | `test_hook_and_cli.py::test_a_match_emits_exactly_the_context_envelope` |
| bad input → exit 0, no output; no match → no stdout at all | `test_bad_input_is_exit_0_and_silent`, `test_no_match_writes_no_stdout_at_all` |
| an engine failure → exit 0, nothing half-emitted, cause on stderr | `test_an_engine_failure_is_exit_0_with_nothing_half_emitted` |
| **the log and state are committed only after the write**: a non-UTF-8 stdout still delivers; a failed write records no row and no state | `test_hardening.py::test_a_non_utf8_stdout_still_delivers_and_exits_0`, `test_a_failed_write_records_no_delivery_and_no_state`, `test_a_successful_write_commits_rows_and_state` |
| deeply nested input → exit 0, silent | `test_deeply_nested_input_is_exit_0_and_silent` |
| the session id comes from the payload only | `test_the_session_comes_from_the_payload_only` |
| a dry-run `check` touches no real state | `test_a_dry_run_check_touches_no_real_state` |
| `install` registers once, keeps existing settings, refuses broken JSON and an unexpected `hooks` shape without a traceback | `test_install_*`, `test_hardening.py::test_install_refuses_an_unexpected_shape_without_a_traceback` |
| `labels add` reports a failed write (exit 2), never "added" | `test_labels_add_reports_a_failed_write` |
| usage errors exit 64 | `test_usage_errors_exit_64` |

## Reports

| Rule | Test |
|---|---|
| every log report is NOT EVALUABLE on an absent or empty log | `test_measure.py::test_every_log_report_is_not_evaluable_on_an_absent_or_empty_log` |
| repeat rate: same rule again in one session; gap sessions for rows without an id | `test_repeat_rate_*` |
| cost splits first full from re-arm full | `test_cost_splits_first_full_from_rearm_full` |
| fire-rate: regressed / held / cleared; an unreadable baseline is NOT EVALUABLE | `test_fire_rate_regressed_held_cleared` |
| cofire groups by the exact call, excludes shadow rows, needs support | `test_cofire_groups_by_the_exact_call_and_needs_support` |
| persistence-loss is computed over joined sessions only, reads subagent transcripts, reads only the text the model was given, and a session with no attachment records is NOT EVALUABLE, never a loss | `test_persistence_loss_*` |
| breadth replays through the real matcher; Bash writes count as eligible only for a `Write`- or `Edit`-scoped rule, so a rate never exceeds 1 and is not diluted; `--sessions` below 1 is a usage error | `test_breadth_replays_through_the_real_matcher_and_never_exceeds_one`, `test_hardening.py::test_breadth_does_not_count_bash_writes_for_a_read_only_rule`, `test_breadth_rejects_a_session_count_below_one` |
| a malformed row is excluded and counted, never read as zero; a crashed report exits 2, never 1; fire-rate with excluded rows and no offender is NOT EVALUABLE | `test_a_malformed_byte_count_is_reported_never_a_finding_or_a_zero`, `test_fire_rate_with_malformed_rows_and_no_offender_is_not_evaluable`, `test_an_unexpected_report_failure_is_exit_2_not_1` |
| labels: absent store NOT EVALUABLE; no precision below n = 20; rule evidence cannot be a false positive; bad lines counted | `test_labels_need_a_store_a_minimum_sample_and_honest_sources` |

## The hook runs from any directory, and budgets hold

| Rule | Test |
|---|---|
| the registered hook command, run by `sh -c` in a directory holding `yaml.py`/`json.py`/`signal.py`, imports none of them and delivers (also in `demo.sh --tour`, which CI runs) | `test_install_registration.py::test_the_registered_command_ignores_modules_in_the_working_directory` |
| `install` recognises and upgrades every older form of its own registration in place, leaving one entry | `test_install_upgrades_an_older_registration_in_place`, `test_install_collapses_duplicate_registrations_of_its_own`, `test_is_ours_*` |
| rules after budget exhaustion are logged `unevaluated`, never `slow`, and `doctor` does not blame them | `test_budget_exhaustion_is_logged_as_unevaluated_not_slow` |
| Bash-write parsing runs under the time budget | `test_bash_write_parsing_of_a_huge_unclosed_quote_is_bounded` |
| an absurd `INSTINCT_MATCH_BUDGET_MS` is clamped | `test_an_enormous_match_budget_does_not_break_matching` |
| a late alarm cannot abort a run | `test_a_stray_alarm_outside_a_guard_is_ignored`, `test_a_timeout_that_escapes_a_guard_is_caught_at_the_call_boundary` |

## Honest messages, checked docs, install on odd settings files

| Rule | Test |
|---|---|
| a Bash-write parse that itself ran out of time is named as that, not blamed on a spent call budget, in the hook message and in `doctor` (old log reasons included) | `test_match_budget.py::test_slow_bash_write_parse_is_named_as_the_cause`, `test_spent_budget_still_blames_the_budget_not_the_rule`, `test_old_log_reason_is_still_read_as_a_parse_timeout` |
| `fire-rate` with no delivered rows, and `breadth` with no eligible call, are NOT EVALUABLE (exit 2) with the cause, never "0 of 0"; `cost` says "n/a" for an undefined share | `test_measure.py::test_fire_rate_with_no_eligible_rows_is_not_evaluable`, `test_breadth_where_no_rule_had_an_eligible_call_is_not_evaluable`, `test_cost_without_full_deliveries_says_why_not_none` |
| the README "Your first rule" block, written and run as shown, loads 1 rule and delivers it | `test_readme_first_rule.py` |
| the README quick-start transcript appears, in order, in the real demo output (CI runs the checker; exit 1 on a mismatch, 2 if it cannot look) | `examples/check_readme.py` (not a pytest test) |
| `install` exits 2 on a settings directory or a dangling symlink, writes through a symlink, keeps the file's mode, writes UTF-8 (non-ASCII not escaped) | `test_install_settings_file.py::test_install_on_a_settings_directory_exits_2_with_a_message`, `test_install_settings_file.py::test_install_refuses_a_dangling_symlinked_settings_file`, `test_install_settings_file.py::test_install_writes_through_a_symlinked_settings_file`, `test_install_settings_file.py::test_install_preserves_the_file_mode`, `test_install_settings_file.py::test_install_writes_non_ascii_as_utf8_not_escapes` |

## Install: the current design

`install` edits a file that belongs to the user and to other tools, so it touches only what is provably its own and checks its own result.

| Rule | Test |
|---|---|
| our command is recognised by EXACT equality with a form this tool registered (bare script, absolute script, `-m instinct_delivery hook`, `-c` + one of `HOOK_CODES` verbatim), never by a substring | `test_install_registration.py::test_recognition_is_exact_not_a_substring`, `test_a_foreign_dash_c_command_naming_the_marker_is_not_ours`, `test_every_released_hook_code_is_recognised`, `test_install_registration.py::test_is_ours_*` |
| our hook lives in its own dedicated entry; another tool's entry (its matcher and its other hooks) is left untouched, and a mixed entry loses only our hook | `test_install_registration.py::test_a_mixed_entry_keeps_its_matcher_and_foreign_hooks`, `test_only_mixed_entries_get_one_new_dedicated_entry`, `test_regex_matchers_are_never_rewritten` |
| a lone dedicated entry keeps its hand-set matcher; several dedicated entries MERGE into one with matcher `".*"`, removing the duplicates by identity (an equal duplicate never takes the kept entry with it), whatever order and matchers they had | `test_install_registration.py::test_a_lone_dedicated_entry_keeps_a_hand_set_matcher`, `test_two_dedicated_entries_collapse_to_one_broad_entry`, `test_install_registration.py::test_duplicate_dedicated_entries_leave_one_current_command`, `test_foreign_entries_survive_a_merge_in_place_and_order` |
| an older registration is upgraded in place; a second run is a no-op | `test_install_registration.py::test_install_is_idempotent_and_recognises_the_old_bare_registration`, `test_install_registration.py::test_install_upgrades_an_earlier_release_command_in_place`, `test_install_registration.py::test_install_is_idempotent_after_a_redesign` |
| the result is checked before and after the write: exactly one entry of ours holding exactly the current command, every other entry unchanged and in order; otherwise exit 2 and, before the write, nothing is changed | `test_install_settings_file.py::test_a_result_that_fails_the_post_write_check_exits_2` |
| the write is atomic through a fresh exclusively-created temp file; a planted link at a temp name is not followed; a failed write leaves the original and no temp file; a new file gets an ordinary mode | `test_install_settings_file.py::test_a_planted_tmp_symlink_is_not_followed`, `test_no_temp_file_is_left_behind`, `test_a_failed_write_leaves_neither_the_file_nor_a_temp`, `test_a_new_file_gets_an_ordinary_mode_not_0600`, `test_a_symlink_to_a_non_regular_file_is_refused` |
| a read-only settings file is not replaced (exit 2); a non-finite number in it (`NaN`) is refused (exit 2), never written as invalid JSON | `test_install_settings_file.py::test_install_respects_a_read_only_settings_file`, `test_install_refuses_non_finite_numbers` |
| a failure to create `.claude/instincts` is not reported as a settings failure | `test_install_settings_file.py::test_an_instincts_dir_failure_is_not_reported_as_a_settings_failure` |

## State, log and hook output

| Rule | Test |
|---|---|
| a state file is written through a fresh temp file in its directory and renamed: a link planted at the old pid-named temp name, or at the state file itself, is never written through | `test_state_and_log_files.py::test_a_planted_link_at_the_state_tmp_name_is_not_followed`, `test_a_state_file_that_is_a_link_is_replaced_not_written_through` |
| the activation log is opened `O_NOFOLLOW`, regular files only: a link is not appended to (also from the real hook, which still exits 0), a FIFO is refused without blocking, a normal log still appends | `test_state_and_log_files.py::test_a_log_that_is_a_link_is_not_appended_to`, `test_the_real_hook_does_not_append_to_a_linked_log`, `test_a_log_that_is_not_a_regular_file_is_refused_without_blocking`, `test_a_normal_log_still_appends` |
| a relative `INSTINCT_STATE_DIR` (or `XDG_STATE_HOME`) is ignored, with a note on stderr for the former; absolute and `~` values are honoured | `test_state_and_log_files.py::test_a_relative_state_dir_is_not_honoured`, `test_an_absolute_and_a_tilde_state_dir_are_honoured`, `test_a_relative_xdg_state_home_is_ignored` |
| the hook exits 0 when its stdout is a full device or a closed pipe (fd 1 is pointed at /dev/null after a failed flush) | `test_cli_robustness.py::test_a_full_stdout_still_exits_0`, `test_a_closed_pipe_still_exits_0` |

## Bash writes: tee

| Rule | Test |
|---|---|
| a `tee` segment (pipeline, `-a`, `sudo`, `--`, quoted name, several files, trailing redirections) is a write to each file operand; `/dev/null`, `$VAR`, no operand and a `tee` that is only a word are not; a heredoc tee is reported once with its body | `test_bash_commands.py::test_tee_writes_are_detected`, `test_tee_near_misses_are_not_writes`, `test_a_heredoc_tee_is_reported_once_with_its_body` |

## Reports

| Rule | Test |
|---|---|
| `cost` leaves rows without a session id out of first/re-arm accounting (they are always full) and reports their bytes separately; the re-arm share is undefined, not 0, with only such rows | `test_report_inputs.py::test_cost_sessionless_rows_are_not_rearm`, `test_cost_only_sessionless_rows_has_an_undefined_rearm_share` |
| a negative `--days` is a usage error (64) | `test_report_inputs.py::test_cost_rejects_negative_days` |

## This file

| Rule | Test |
|---|---|
| every test named in this file exists, and the check fails on a row that names nothing (negative control) | `test_docs_match_code.py::test_every_invariants_row_names_an_existing_test`, `test_the_invariants_check_fails_on_a_row_that_names_nothing` |

## Reports: what counts as delivered

| Rule | Test |
|---|---|
| a truncated delivery is not a full delivery: it leaves no full-delivery stamp and never claims the full text was delivered; a raised budget then delivers the full advice; a full delivery still suppresses | `test_partial_delivery.py::test_truncated_then_budget_raised_delivers_the_full_advice`, `test_truncated_never_claims_full_text_was_delivered`, `test_truncation_leaves_no_full_delivery_stamp`, `test_control_full_delivery_still_suppresses` |
| one huge `confidence` rejects only its own rule; `doctor` names it | `test_rule_loading.py::test_loader_rejects_a_huge_integer_confidence_and_keeps_valid_rules`, `test_hook_still_delivers_the_valid_rule`, `test_doctor_names_the_rejected_rule`, `test_control_ordinary_and_large_finite_confidence_load` |
| session ids map to state files injectively | `test_state_and_log_files.py::test_generated_stem_does_not_collide_with_its_literal_spelling`, `test_a_full_delivery_in_one_does_not_suppress_the_other`, `test_control_safe_ids_stay_literal_and_unsafe_ids_stay_distinct` |
| `labels stats` on only unreadable lines, `repeat-rate` on rows no session can be assigned to, and `cofire` on fewer calls than `--min-support` are NOT EVALUABLE (exit 2) with the cause | `test_report_inputs.py::test_labels_stats_on_only_unreadable_lines_is_not_evaluable`, `test_repeat_rate_with_no_assignable_session_names_the_cause`, `test_cofire_on_fewer_calls_than_min_support_is_not_evaluable`, `test_cofire_with_enough_calls_still_reports` |

## One bad input costs only itself

| Rule | Test |
|---|---|
| a regex nested thousands deep, YAML nested thousands deep, and a YAML alias bomb each reject only their own file (cause named, reason bounded), the good rule still loads and delivers, `doctor` exits 1 with no traceback | `test_rule_loading.py::test_one_bad_rule_file_is_rejected_and_the_good_rule_still_loads`, `test_the_hook_still_delivers_the_good_rule`, `test_doctor_reports_each_and_exits_1_without_a_traceback` |
| an alias bomb is rejected in under 0.5 s with a small peak allocation; anchors are refused too; huge bad values are not echoed whole | `test_rule_loading.py::test_an_alias_bomb_is_rejected_fast_and_small`, `test_an_anchor_without_an_alias_is_refused_too`, `test_a_huge_bad_value_is_not_echoed_whole`, `test_a_huge_wrong_typed_confidence_is_not_echoed_whole` |
| the exit-1 lists in README and docs/reports.md name a BROAD `breadth` rule | `test_docs_match_code.py::test_the_exit_one_lists_name_breadth` |
| `install` on a deeply nested settings file exits 2 and changes nothing | `test_install_settings_file.py::test_install_on_a_deeply_nested_settings_file_exits_2` |
| a state file that is a FIFO or a link is not read (no hang); empty state results | `test_state_and_log_files.py::test_a_fifo_at_the_state_file_does_not_hang`, `test_a_symlinked_state_file_is_not_read` |
| a session id with a lone surrogate still gets its rows written | `test_state_and_log_files.py::test_a_lone_surrogate_session_id_still_writes_its_rows` |
| stale `.*.tmp` files in the delivery dir are pruned, fresh ones kept | `test_state_and_log_files.py::test_stale_temp_files_are_pruned_and_fresh_ones_kept` |
| the log and state files are created 0600 whatever the umask; SECURITY.md says so | `test_state_and_log_files.py::test_the_log_and_state_files_are_created_0600`, `test_security_md_documents_the_modes` |
| the README empty-log comment matches the real message; DESIGN.md names no private review file | `test_docs_match_code.py::test_the_readme_empty_log_comment_matches_the_real_message`, `test_design_md_does_not_point_at_a_private_review_file` |

## Bounded loading, honest writes

| Rule | Test |
|---|---|
| rule loading is bounded: 40 large legitimate files load inside a time bound; files past the total-size or file-count cap are REJECTED unparsed with the cap named (count excess in one line); `doctor` exits 1; the caps are in docs/rules.md and SECURITY.md | `test_rule_loading.py::test_forty_large_legitimate_files_load_inside_a_time_bound`, `test_files_past_the_total_byte_cap_are_rejected_with_the_cap_named`, `test_files_past_the_count_cap_are_rejected_in_one_named_entry`, `test_doctor_names_the_cap_and_exits_1`, `test_the_caps_are_documented` |
| `install` on a settings file holding a lone surrogate exits 2, leaves the file unchanged and no temp file | `test_install_settings_file.py::test_install_on_a_lone_surrogate_settings_file_exits_2_and_changes_nothing` |
| Bash write detection claims no phantom write: arithmetic, double-bracket tests, `->`, dups and stderr redirects are not writes; a command substitution is a command of its own (no stray `)` in a target); near misses still write | `test_bash_commands.py::test_no_phantom_write_paths`, `test_arithmetic_comparison_is_not_a_write`, `test_a_command_substitution_is_its_own_segment`, `test_near_misses_still_write`, `test_a_subshell_closing_paren_is_not_part_of_the_target`, `test_a_substitution_nested_deeply_does_not_crash`, `test_unclosed_substitutions_repeated_do_not_go_quadratic` |
| an echo redirect keeps all of its content, a digit ending a word is not an fd, only a lone digit token is, `echo>f` is seen, and a content rule fires on it | `test_bash_commands.py::test_content_is_not_cut_when_the_redirect_has_no_space`, `test_a_digit_ending_a_word_is_not_an_fd`, `test_only_a_lone_digit_token_is_an_fd`, `test_echo_directly_followed_by_a_redirect_is_seen`, `test_the_leaked_secret_now_fires_a_content_rule` |
| `doctor` names the real cause of an unreadable or non-directory rules path (exit 2) and `load_rules` never raises on one | `test_rule_loading.py::test_doctor_names_a_permission_error_on_the_rules_dir`, `test_doctor_says_what_a_non_directory_rules_path_is`, `test_load_rules_never_raises_on_an_unreadable_dir` |
| an unresolvable `~user` in INSTINCT_RULES_DIR / INSTINCT_STATE_DIR is exit 2 with the setting named for commands, and exit 0 for the hook | `test_cli_robustness.py::test_an_unexpandable_rules_dir_is_exit_2_with_a_message`, `test_an_unexpandable_state_dir_is_exit_2_for_commands`, `test_the_hook_stays_exit_0_on_an_unexpandable_dir` |
| a lone surrogate in any rule text field rejects only that file; no log row is dropped | `test_rule_loading.py::test_a_lone_surrogate_in_any_text_field_rejects_the_file`, `test_a_rule_row_is_never_dropped_from_the_log` |
| a heredoc body is the content written only for commands that copy stdin; otherwise the write has empty content | `test_heredocs.py::test_a_heredoc_for_another_command_is_not_the_content_written`, `test_commands_that_copy_stdin_still_write_the_body`, `test_a_cat_redirect_is_a_write_of_unknown_content` |
| new state, delivery and log directories are 0700 whatever the umask; an existing one keeps its mode; SECURITY.md says so | `test_state_and_log_files.py::test_new_state_and_log_directories_are_0700_whatever_the_umask`, `test_an_existing_directory_keeps_its_mode`, `test_security_md_documents_the_directory_mode` |
| `cost` on a log with only slow/shadow/notice/unevaluated rows is NOT EVALUABLE (exit 2) with the denominator; one delivery row still reports | `test_report_inputs.py::test_cost_on_slow_and_shadow_rows_only_is_not_evaluable`, `test_cost_with_one_delivery_row_still_reports` |

## The command line survives its console; Bash writes read what the docs say

| Rule | Test |
|---|---|
| a console that cannot encode `—`, `·` or `⚠️` (ASCII, the C locale) or an argument holding a non-UTF-8 byte never crashes a command: text prints escaped, `repeat-rate` on a missing log and `doctor` exit 2, `check` delivers, `install` exits 0 after writing; a UTF-8 console still gets the real characters; a report whose print fails is exit 2, never 1 | `test_cli_robustness.py::test_a_report_on_a_missing_log_does_not_crash_on_a_dash`, `test_doctor_and_check_survive_a_console_that_cannot_encode`, `test_a_utf8_console_still_gets_the_real_characters`, `test_a_non_utf8_byte_in_an_argument_does_not_crash_doctor`, `test_install_into_a_non_utf8_directory_exits_0_after_writing`, `test_a_report_whose_print_fails_is_exit_2_never_1` |
| a Bash write is found inside compound statements and behind keywords, assignments, `sudo`/`env`/`command`/`nice`, an absolute command path, `\|&`, `>\|`, `>& f`, several redirects; the content survives the prefix; prefixes invent no write; a line continuation joins the lines | `test_bash_commands.py::test_writes_in_compound_statements_and_behind_prefixes_are_found`, `test_content_survives_a_prefix`, `test_quiet_direction_prefixes_do_not_invent_writes`, `test_a_line_continuation_joins_the_lines` |
| a backslash-escaped quote does not break quote tracking; inside single quotes a backslash escapes nothing | `test_bash_quoting.py::test_escaped_quotes_do_not_break_quote_tracking`, `test_content_keeps_the_escaped_quote_and_single_quotes_do_not_escape` |
| no phantom write: a comment, an escaped `>`, `/dev/tty`, `/dev/fd/N`; a `#` inside a word or quotes is not a comment; `sed` options are parsed (not taken for operands) in GNU and BSD forms and `sed` without `-i` or files is no write; a target with a command substitution leaves no stub (the inner write is still found); echo content carries no redirect text | `test_bash_quoting.py::test_no_phantom_write`, `test_a_hash_inside_a_word_or_quotes_is_not_a_comment`, `test_sed_options_are_parsed_not_taken_for_operands`, `test_sed_without_in_place_or_files_is_no_write`, `test_a_command_substitution_in_a_target_leaves_no_garbage_path`, `test_echo_content_has_no_redirect_text` |
| docs/rules.md lists the forms read and the known unread ones, and the code agrees with both lists | `test_docs_match_code.py::test_the_docs_list_exactly_the_forms_the_code_reads_and_the_unread_ones_stay_unread` |
| `check` on a missing, non-directory or unreadable rules path is NOT EVALUABLE (stderr, exit 2, nothing on stdout); the hook stays exit 0 and names the cause once on stderr; a readable or empty directory is silent | `test_rule_loading.py::test_check_on_a_missing_rules_dir_is_not_evaluable_with_the_cause`, `test_check_on_a_file_instead_of_a_directory_says_what_it_is`, `test_check_on_an_unreadable_rules_dir_names_the_permission`, `test_the_hook_stays_exit_0_and_names_the_cause_on_stderr`, `test_quiet_direction_a_readable_dir_is_silent_and_check_exits_0` |
| `--json` is rejected on `check` and `doctor` (it did nothing) and still honoured on the reports; `check --state-dir` needs `--record`; `--record` and `--scratch-state` exclude each other | `test_cli_robustness.py::test_json_is_rejected_where_it_does_nothing`, `test_json_is_still_honoured_where_it_works`, `test_state_dir_on_a_dry_run_check_is_a_usage_error`, `test_state_dir_with_record_writes_there_and_record_excludes_scratch` |
| a lone surrogate in a tool input, tool name or session id still writes the log row (spelled as an escape); every row kind is encodable; a session id over 128 characters is cut with a digest and two long ids stay distinct sessions | `test_state_and_log_files.py::test_a_lone_surrogate_in_tool_input_still_writes_the_log_row`, `test_every_string_in_every_row_kind_is_made_encodable`, `test_a_session_id_is_capped_in_the_row_and_stays_distinct` |
| a directory, FIFO, dangling or looping link named `x.yaml` is `NOT READ` with its reason (`doctor` exit 1, the load never opens it); a link to a real rule is read | `test_rule_loading.py::test_a_dangling_symlink_a_fifo_and_a_directory_are_named_not_read`, `test_quiet_direction_a_symlink_to_a_real_rule_is_read` |
| `install` says "upgraded" only when a dedicated entry of ours existed | `test_install_registration.py::test_install_does_not_say_upgraded_when_it_only_moved_out_of_a_mixed_entry`, `test_install_still_says_upgraded_for_an_older_dedicated_entry` |
| a too-large repetition count and a POSIX class (`[[:alpha:]]`) are rejected with their own named cause and no Python warning reaches the hook | `test_rule_loading.py::test_a_huge_repetition_count_is_rejected_with_its_own_cause`, `test_a_posix_class_is_rejected_with_a_named_cause_and_no_warning`, `test_the_hook_prints_no_python_warning_for_a_posix_class` |
| `fire-rate --byte-cap` below 1 is a usage error; a bad `--baseline` names its cause | `test_report_inputs.py::test_fire_rate_byte_cap_must_be_positive`, `test_a_bad_baseline_names_the_cause_not_a_raw_exception` |
| `--days 0`, unparsable timestamps and future-dated rows are named, counted and excluded from a window (a future row is not "the last 7 days"); a list where `rule`/`session_id`/`ts` should be text is a counted malformed row in `repeat-rate`, `cofire` and `fire-rate` too | `test_report_inputs.py::test_cost_days_0_names_the_zero_window`, `test_rows_with_an_unparsable_timestamp_are_counted_and_named`, `test_future_rows_are_excluded_and_said_so`, `test_unhashable_fields_are_malformed_rows_in_every_report` |
| `breadth` matches each call under the per-call time budget, counts a skipped call and leaves it out of the rate's denominator | `test_match_budget.py::test_breadth_replay_has_a_time_budget_per_call` |

## The command a quoted assignment hides, the heredoc that belongs to one command, linear parsing

| Rule | Test |
|---|---|
| a `VAR=value` assignment is recognised by its unquoted `NAME=` prefix whatever quotes the value has, so it never hides the command; a quoted name is a command, an assignment alone writes nothing | `test_bash_commands.py::test_a_quoted_or_substituted_assignment_does_not_hide_the_command`, `test_tee_behind_a_quoted_assignment`, `test_a_quoted_name_is_a_command_not_an_assignment`, `test_quiet_direction_an_assignment_alone_writes_nothing` |
| a heredoc body belongs only to the command that owns its `<<TAG` and reaches a later command only through a pipe from `cat`/`tee` with no stdout redirect; a `cat`/`tee` without one gets empty content | `test_heredocs.py::test_a_heredoc_body_belongs_to_the_command_that_owns_the_operator`, `test_a_second_command_on_the_line_gets_nothing_from_the_body`, `test_a_pipe_still_carries_the_body_to_the_next_command`, `test_a_pipe_through_another_command_or_a_redirect_carries_no_content`, `test_the_heredoc_owner_keeps_its_own_body_in_every_position` |
| `${ }` and `$[ ]` holding a `>` are not redirects, a `;` or `\|` inside does not split the command, nesting is bounded, the real redirect after one is still found | `test_bash_quoting.py::test_an_expansion_holding_a_gt_is_not_a_write`, `test_the_real_redirect_after_an_expansion_is_still_found`, `test_a_semicolon_or_pipe_inside_an_expansion_does_not_split_the_command`, `test_nested_and_unbalanced_braces_are_bounded` |
| `fire-rate` with excluded rows (malformed, future, unstamped) and no new offender is exit 2 printing NOT EVALUABLE with the counts, in text and `--json`; clean without exclusions; a new offender is still exit 1; every exit 2 of a report starts with NOT EVALUABLE | `test_report_inputs.py::test_fire_rate_with_excluded_rows_and_no_offender_says_not_evaluable`, `test_the_same_in_json`, `test_quiet_direction_no_excluded_rows_and_no_offender_is_clean`, `test_a_new_offender_is_still_a_finding_while_rows_are_excluded`, `test_every_report_that_returns_a_code_never_prints_a_report_for_exit_2` |
| `breadth` parses each Bash command under the per-call time budget; an unterminated `[[ ` is linear; a real `[[ ]]` test is still not a write; every scanner stays under a time bound on 50k and 200k characters of repetitions of its own opening token (`[[ `, `${`, `$[`, `$((`, `((`, `<<`, `<<EOF` lines, `$(`, backtick, `{`, `(`, quotes, `$'`, `>(`) | `test_match_budget.py::test_breadth_bounds_the_bash_write_parse_of_a_call`, `test_has_writes_runs_under_the_per_call_budget`, `test_an_unterminated_double_bracket_is_linear`, `test_a_double_bracket_test_is_still_not_a_write`, `test_every_scanner_is_linear_on_repetitions_of_its_own_opening_token` |
| `{ ...; } > f` and `( ... ) > f` are writes of unknown content; to a dup, a sink or stderr they are not | `test_bash_commands.py::test_a_group_redirect_is_a_write_of_unknown_content`, `test_quiet_direction_a_group_redirect_to_a_dup_or_sink_or_stderr_is_no_write` |
| ANSI-C `$'...'` is one quoted word (a `\'` inside does not end it); a backslash-quoted heredoc tag is a heredoc | `test_bash_quoting.py::test_ansi_c_quoting_is_parsed_not_garbled`, `test_ansi_c_quoting_hides_a_redirect_inside_it`, `test_a_backslash_quoted_heredoc_tag_is_recognised` |
| wrapper long options with values are skipped (`sudo --user bob`, `time -f %e`, `env --unset X`), and the value is not taken for the command | `test_bash_commands.py::test_wrapper_long_options_with_values_are_skipped`, `test_quiet_direction_a_wrapper_option_value_is_not_taken_for_the_command` |
| a NOT EVALUABLE report is JSON with `--json`; text is unchanged; a too-large `--days` and a directory as `--log` name their cause | `test_report_inputs.py::test_a_not_evaluable_report_is_json_with_json`, `test_text_is_unchanged_without_json`, `test_a_huge_days_names_the_cause`, `test_a_directory_as_the_log_names_the_cause`, `test_quiet_direction_a_large_but_valid_days_still_reports` |
| the log writes `null` for an absent domain or confidence, and the value when present; the docs say so | `test_rule_rendering.py::test_a_rule_without_domain_or_confidence_logs_null`, `test_a_rule_with_them_logs_them`, `test_the_docs_say_null` |
| `check` names (stderr, exit 0) a tool input that is not a JSON object and a tool no loaded rule is scoped to, and says nothing extra otherwise | `test_cli_robustness.py::test_check_names_an_input_that_is_not_an_object`, `test_check_names_a_tool_no_rule_mentions`, `test_quiet_direction_a_scoped_tool_and_an_object_input_say_nothing_extra` |
| the docs call the backtracking rejection a structural check of known shapes with the time budget as backstop; `(a?){30}a{30}` and `(a{1,50}){1,50}$` load | `test_docs_match_code.py::test_the_docs_call_the_backtracking_check_structural_with_the_budget_as_backstop`, `test_those_patterns_do_load` |

## Quotes nested in `${ }`, any heredoc tag, the exact terminator, linear `python -`

| Rule | Test |
|---|---|
| quotes nested in a double-quoted `${ }` start fresh: no phantom write or split from `>`, `;`, `\|`, `#` inside; the real redirect after one is found; plain quotes and expansions unchanged | `test_bash_quoting.py::test_quotes_nested_in_a_dquoted_expansion_start_fresh_and_hold_no_write`, `test_the_real_redirect_after_one_is_found_with_sensible_content`, `test_quiet_direction_plain_quotes_and_expansions_still_work` |
| a heredoc tag is any shell word and quote removal decides the terminator; two heredocs on a line each take their body | `test_heredocs.py::test_a_heredoc_tag_is_any_shell_word`, `test_the_reported_case`, `test_quote_removal_decides_the_terminator`, `test_quiet_direction_a_tag_is_not_taken_from_a_here_string_or_arithmetic`, `test_two_heredocs_on_one_line_each_have_their_body` |
| a terminator is the exact line (`<<-`: leading TABs only, stripped from the body); ` EOF`, `EOF ` and `EOF\r` do not end a heredoc | `test_heredocs.py::test_an_indented_tag_line_does_not_terminate`, `test_a_tab_indented_tag_terminates_only_with_dash`, `test_a_dash_heredoc_strips_tabs_only_not_spaces`, `test_a_trailing_space_or_cr_does_not_terminate`, `test_quiet_direction_the_exact_line_still_terminates` |
| `abc12>f` is documented as a write, not under "Not writes" | `test_docs_match_code.py::test_the_docs_do_not_list_a_digit_ending_a_word_under_not_writes` |
| the `python -` write finder is linear (8000 assignments under 1 s) and keeps its semantics | `test_bash_scan_limits.py::test_python_writes_is_linear_in_the_number_of_assignments`, `test_and_through_the_heredoc`, `test_the_semantics_are_unchanged` |
| `cat file <<A` carries nothing, `cat -n`/`cat -` do; a stdout redirect of `cat` carries nothing down a pipe, a stderr redirect still does; the last heredoc of a command is its stdin | `test_bash_commands.py::test_cat_with_a_file_operand_ignores_stdin`, `test_quiet_direction_cat_options_and_dash_read_stdin`, `test_a_stdout_redirect_of_cat_carries_nothing_through_the_pipe`, `test_quiet_direction_a_stderr_redirect_still_carries`, `test_the_last_heredoc_of_a_command_is_its_stdin` |
| only the last stdout redirect receives the text (earlier files are created empty); `$"..."` loses its `$`; `(( x )) > f` and `A="b" > f` behave as documented | `test_bash_commands.py::test_the_text_goes_only_to_the_last_stdout_redirect`, `test_a_locale_string_loses_its_dollar`, `test_arithmetic_command_and_bare_assignment_redirects` |
| the example scripts ignore the caller's `INSTINCT_*` settings | `test_examples.py::test_the_example_scripts_do_not_inherit_the_instinct_environment`, `test_demo_sh_runs_clean_with_a_foreign_rules_dir_in_the_environment` |
| a scan that spent its allowance marks `out_of_budget` and the call is logged `unevaluated`; a normal command is not marked | `test_match_budget.py::test_a_spent_substitution_scan_is_marked_not_silently_dropped`, `test_match_names_the_spent_scan_as_unevaluated` |

## Heredocs as bash reads them, cp, python owner, backticks, linear open()

| invariant | test |
|---|---|
| an unterminated heredoc's body runs to the end and nothing after it is a command | `test_heredocs.py::test_an_unterminated_heredoc_body_runs_to_the_end` |
| `cp` is not a stdin copier | `test_bash_commands.py::test_cp_is_not_a_stdin_copier` |
| the python write finder is linear on unclosed `open(` | `test_bash_scan_limits.py::test_python_writes_is_linear_on_unclosed_opens` |
| `python -` is judged on the command that owns the heredoc | `test_bash_commands.py::test_python_in_an_earlier_command_does_not_own_a_cat_heredoc`, `test_bash_commands.py::test_a_cat_body_before_a_python_body_is_not_python` |
| quote state carries across lines; a backslash-newline joins lines | `test_heredocs.py::test_a_quote_open_across_lines_does_not_hide_a_later_heredoc`, `test_heredocs.py::test_a_heredoc_marker_inside_a_multiline_string_is_text`, `test_bash_quoting.py::test_a_line_continuation_before_the_redirect` |
| `<<` inside arithmetic or an expansion is a shift | `test_heredocs.py::test_shift_operators_do_not_start_a_heredoc` |
| `$'..'`, `$".."` and continued tags | `test_heredocs.py::test_dollar_quoted_and_continued_tags` |
| backticks unescape as bash does | `test_bash_quoting.py::test_backtick_escapes`, `test_bash_quoting.py::test_nested_escaped_backticks_find_the_inner_write` |
| a heredoc in `$( )` keeps its body; an unquoted body expands `$( )` | `test_heredocs.py::test_a_heredoc_inside_a_command_substitution_keeps_its_body`, `test_heredocs.py::test_an_unquoted_heredoc_body_expands_command_substitutions` |

## Linear continuations, literal quotes in bodies, "$( )" heredocs, body continuations

| invariant | test |
|---|---|
| backslash-newline joining is linear | `test_bash_scan_limits.py::test_backslash_newline_joining_is_linear` |
| a quote in an unquoted body does not hide a substitution | `test_heredocs.py::test_a_quote_in_an_unquoted_body_does_not_hide_a_substitution` |
| a heredoc inside "$( )" keeps its write | `test_heredocs.py::test_a_heredoc_inside_a_double_quoted_substitution_keeps_its_write` |
| an unquoted body joins continuations before the terminator; a quoted one does not; backslash pairs collapse; `<<-` strips TABs at a joined line's start only | `test_heredocs.py::test_a_continuation_in_an_unquoted_body_is_joined_before_the_terminator`, `test_heredocs.py::test_a_quoted_tag_keeps_its_continuations`, `test_heredocs.py::test_backslash_pairs_in_an_unquoted_body_collapse`, `test_heredocs.py::test_dash_heredoc_strips_tabs_only_at_the_start_of_a_joined_line` |
| `python -` must be the command word | `test_bash_commands.py::test_python_must_be_the_command_word`, `test_bash_commands.py::test_python_behind_options_is_still_python` |
| a rule holding a hidden character in any field is rejected with the code point; emoji and accents load | `test_hidden_unicode.py::test_an_action_with_a_hidden_character_is_rejected_with_its_code_point`, `test_hidden_unicode.py::test_hidden_characters_in_any_other_field_are_rejected_too`, `test_hidden_unicode.py::test_ordinary_text_emoji_and_accents_still_load`, `test_hidden_unicode.py::test_doctor_names_the_hidden_character_and_exits_nonzero` |
| `test` runs each rule's own cases against the real matcher, per rule, Bash writes included; malformed cases reject the rule; no cases is NOT EVALUABLE | `test_rule_tests.py::test_passing_and_failing_cases_are_reported_per_case`, `test_rule_tests.py::test_a_case_tests_only_its_own_rule`, `test_rule_tests.py::test_bash_writes_are_tested_the_way_the_hook_sees_them`, `test_rule_tests.py::test_a_malformed_tests_block_rejects_the_rule`, `test_rule_tests.py::test_no_cases_at_all_is_not_evaluable` |
| a full delivery drops no text at any `splitlines()` boundary in the first line | `test_rule_rendering.py::test_a_full_delivery_drops_no_text_at_any_line_break`, `test_rule_rendering.py::test_the_line_break_set_is_every_splitlines_boundary` |
| the tag block and unassigned points are rejected; a presentation selector only after a visible character; U+200D only between two emoji | `test_hidden_unicode.py::test_unassigned_and_tag_block_points_are_rejected`, `test_hidden_unicode.py::test_the_whole_tag_block_is_rejected`, `test_hidden_unicode.py::test_a_presentation_selector_anywhere_else_is_rejected`, `test_hidden_unicode.py::test_an_emoji_zwj_sequence_loads`, `test_hidden_unicode.py::test_a_zwj_anywhere_else_is_rejected`, `test_hidden_unicode.py::test_an_emoji_family_loads_from_a_rule_file` |
| `follow-rate` with no joined delivery is NOT EVALUABLE (followed null) with counts; no base-rate percentage below min-n; an escaping session id is never joined | `test_follow_rate.py::test_no_deliveries_is_not_evaluable_not_a_measured_zero`, `test_follow_rate.py::test_unjoined_deliveries_are_not_evaluable_with_the_cause_and_counts`, `test_follow_rate.py::test_below_min_n_the_base_rate_prints_counts_only`, `test_follow_rate.py::test_a_session_id_that_escapes_the_transcripts_dir_is_refused` |
| every shipped example and every file of every pack under `examples/packs` carries fires and silent cases, loads whole, and passes | `test_rule_tests.py::test_every_example_rule_carries_cases_and_they_pass` (every yaml file, retired included) |
| `follow-rate` counts a follow only inside `within` later calls, beside a base rate over every window; no predicate or no join is NOT EVALUABLE; small samples print counts, no rate | `test_follow_rate.py::test_a_follow_inside_the_window_counts_and_one_outside_does_not`, `test_follow_rate.py::test_the_base_rate_counts_every_window_in_the_joined_sessions`, `test_follow_rate.py::test_a_rule_without_a_follow_predicate_is_not_evaluable_not_zero`, `test_follow_rate.py::test_below_the_minimum_sample_the_counts_print_but_no_rate`, `test_follow_rate.py::test_no_delivery_joins_is_not_evaluable` |
| only delivered kinds count; unjoined deliveries leave the denominator; the predicate uses the real matcher | `test_follow_rate.py::test_shadow_and_withheld_rows_are_not_deliveries`, `test_follow_rate.py::test_a_delivery_with_no_later_call_or_no_transcript_is_not_in_the_denominator`, `test_follow_rate.py::test_the_follow_predicate_uses_the_real_matcher_including_bash_writes` |

## audit (look at the fires)

| invariant | test |
|---|---|
| the sample is deterministic for a log and window end, changes with the window end, holds at most n real matches per rule (shadow in; notice/slow/unevaluated/muted/budget out) | `test_audit.py::test_the_sample_is_deterministic_and_changes_with_the_window_end`, `test_audit.py::test_a_sample_is_at_most_n_per_rule_and_only_real_matches` |
| each fire is joined to the latest earlier call of its tool in its own session; a failed join says `call not found`; the head is one line of at most 160 chars | `test_audit.py::test_the_triggering_call_is_joined_from_the_transcript`, `test_audit.py::test_call_not_found_is_said_not_blanked`, `test_audit.py::test_the_head_is_one_line_and_at_most_160_chars` |
| a rule whose fires matched one bare word is BARE (exit 1); situations and regexes are not; below n = 5 the flag is NOT EVALUABLE with the denominator and no rate | `test_audit.py::test_a_rule_that_fired_on_one_bare_word_is_flagged_BARE_and_exits_1`, `test_audit.py::test_a_rule_whose_fires_matched_situations_is_not_flagged`, `test_audit.py::test_a_regex_is_not_a_bare_word`, `test_audit.py::test_under_five_fires_the_flag_is_not_evaluable_and_no_rate_prints`, `test_audit.py::test_what_counts_as_a_bare_token`, `test_audit.py::test_json_carries_the_denominators` |
| no rows in the window, a missing transcripts dir, or an unknown rule is NOT EVALUABLE (2); bad usage is 64 | `test_audit.py::test_no_rows_in_the_window_missing_transcripts_and_unknown_rule_are_exit_2`, `test_audit.py::test_a_bad_usage_is_64` |
| `audit label` writes a transcript_sample label that `labels stats` counts; the id is content-addressed and looked up in the log; an id no row has is refused (64) and writes nothing | `test_audit.py::test_a_label_round_trips_into_labels_stats`, `test_audit.py::test_an_unknown_fire_id_is_refused_and_writes_nothing`, `test_audit.py::test_ids_are_content_addressed_and_survive_a_new_fire_n_and_the_window_date`, `test_audit.py::test_parallel_same_tool_calls_join_the_one_the_rule_matches` |
| `--brief` names the bare tokens and the options and never edits a rule file | `test_audit.py::test_brief_names_the_bare_tokens_and_options_and_edits_nothing`, `test_audit.py::test_brief_on_a_rule_with_no_file_still_prints_and_says_so` |
| a regex rule is judged on the text it matched, recovered locally from the joined call (never logged); un-joined fires fall back to the logged key and are labelled; a call the rule no longer matches is said and excluded | `test_audit.py::test_a_regex_rule_whose_real_matches_are_bare_words_is_flagged_from_the_recovered_text`, `test_audit.py::test_a_regex_rule_matching_a_situation_is_not_flagged`, `test_audit.py::test_a_joined_call_the_rule_no_longer_matches_is_said_and_not_guessed`, `test_audit.py::test_the_recovered_text_is_one_line_and_at_most_80_chars` |

## Order across calls (`after`), `before` cases, `replay`, the agent-security pack

| invariant | test |
|---|---|
| `after` is built like `follow` (default window 20, bounds 1-200); a malformed block rejects the whole rule with its reason | `test_after.py::test_after_is_built_like_follow_with_a_default_window_of_20`, `test_after.py::test_a_malformed_after_rejects_the_whole_rule_with_its_reason`, `test_after.py::test_within_accepts_the_bounds` |
| a rule fires only after the precondition; **negative control:** the same call with no earlier match is silent, and one call never satisfies itself | `test_after.py::test_fires_only_after_the_precondition_and_not_before`, `test_after.py::test_one_call_matching_both_does_not_satisfy_itself` |
| the window counts every call of the session; a later match renews it; sessions do not share a precondition | `test_after.py::test_within_counts_every_call_of_the_session`, `test_after.py::test_a_later_match_of_the_precondition_renews_the_window`, `test_after.py::test_sessions_do_not_share_a_precondition` |
| no session id: the rule does not fire and an `unevaluated` row names the cause; a call its own filters do not match logs nothing; `doctor` and the message do not blame the time budget | `test_after.py::test_no_session_id_cannot_be_evaluated_and_says_why`, `test_after.py::test_no_session_id_and_no_own_match_logs_nothing`, `test_after.py::test_doctor_names_an_after_rule_that_could_not_be_evaluated_and_why`, `test_after.py::test_the_unevaluated_message_for_after_does_not_blame_the_time_budget` |
| the state holds counters only, never call content; it is written on calls that match nothing and on a shadow-only hit, and not at all when no loaded rule has `after` | `test_after.py::test_state_holds_counters_only_never_content`, `test_after.py::test_state_is_written_on_calls_that_match_nothing`, `test_after.py::test_the_sequence_is_saved_on_a_call_whose_only_hit_is_a_shadow_rule`, `test_after.py::test_no_after_rule_means_no_state_is_read_or_written_on_a_quiet_call` |
| damaged or future sequence state never makes a rule fire; repeat-off and delivery suppression coexist with it | `test_after.py::test_damaged_sequence_state_reads_as_no_earlier_call`, `test_after.py::test_a_future_stamp_in_state_does_not_satisfy_the_rule`, `test_after.py::test_repeat_off_still_remembers_the_precondition`, `test_after.py::test_delivery_suppression_still_works_beside_the_sequence_state` |
| `check` says an `after` rule needs an earlier call (and is quiet when the rule did not match on its own); a shadow `after` rule logs only once satisfied | `test_after.py::test_check_says_an_after_rule_was_not_evaluated`, `test_after.py::test_check_is_quiet_about_after_when_the_rule_did_not_match_on_its_own`, `test_after.py::test_a_shadow_after_rule_is_logged_as_shadow_only_once_satisfied` |
| `breadth` replays each file in order with a fresh sequence (no leak between files); `audit` re-runs own keys only and says so, never "the rule changed" | `test_after.py::test_breadth_replays_each_transcript_in_order_with_a_fresh_sequence_per_file`, `test_after.py::test_breadth_does_not_carry_a_precondition_from_one_transcript_into_the_next`, `test_after.py::test_breadth_of_a_rule_without_after_is_unchanged_by_file_boundaries`, `test_after.py::test_audit_judges_an_after_rule_on_its_own_keys_and_says_the_precondition_was_not_rerun` |
| the docs quote the cause string, the state key and the bounds the code uses | `test_after.py::test_the_docs_quote_the_cause_and_the_state_key_the_code_uses` |
| a `before` history makes an `after` rule fire; no history keeps it silent; only the own call is judged; each case has a fresh session; the real state dir is untouched; a malformed `before` rejects the rule | `test_before_cases.py::test_a_before_list_makes_an_after_rule_fire_and_no_history_keeps_it_silent`, `test_before_cases.py::test_expect_applies_to_the_own_call_only_not_to_the_before_calls`, `test_before_cases.py::test_each_case_gets_a_fresh_session`, `test_before_cases.py::test_before_cases_never_touch_the_real_state_dir`, `test_before_cases.py::test_before_is_loaded_whole_or_the_rule_is_rejected`, `test_before_cases.py::test_a_case_without_before_on_an_after_rule_goes_through_the_engine` |
| `replay` runs the real engine per session and reports each delivery by row; skipped rows are counted by reason; it writes nothing outside its throwaway dir | `test_replay.py::test_replay_runs_the_real_engine_per_session_and_reports_each_delivery`, `test_replay.py::test_replay_counts_what_it_skipped_and_why`, `test_replay.py::test_replay_writes_nothing_outside_its_throwaway_dir`, `test_replay.py::test_replay_summarises_per_session_and_in_total` |
| `replay` is NOT EVALUABLE (2) for an unreadable file, a file with no replayable row (counts shown), or a missing rules dir; a row with no session replays as no session | `test_replay.py::test_an_unreadable_file_is_not_evaluable`, `test_replay.py::test_a_file_with_no_replayable_row_is_not_evaluable_with_the_counts`, `test_replay.py::test_a_missing_rules_dir_is_not_evaluable_not_a_silent_replay`, `test_replay.py::test_rows_without_a_session_replay_with_no_session_and_say_so` |
| the agent-security pack: every rule loads whole and is documented; each has fires and silent cases and passes them | `test_agent_security_pack.py::test_the_pack_loads_whole_and_every_rule_is_documented`, `test_agent_security_pack.py::test_the_pack_cases_pass_and_each_rule_has_both_directions`, `test_agent_security_pack.py::test_the_sequence_rules_carry_history_in_their_cases` |
| replayed over the attack/benign fixture, each attack session gets the expected rule at the expected step, no benign twin gets any delivery, and the benign insert is silent only because no secret was read first | `test_agent_security_pack.py::test_each_attack_session_gets_the_expected_rule_at_the_expected_step`, `test_agent_security_pack.py::test_no_benign_twin_gets_any_delivery_from_the_pack`, `test_agent_security_pack.py::test_the_benign_insert_stays_silent_only_because_no_secret_was_read_first` |
| the flow the pack does not catch (a value compared across calls) delivers nothing, and the pack README says so | `test_agent_security_pack.py::test_the_github_flow_is_not_caught_and_the_pack_says_so` |
