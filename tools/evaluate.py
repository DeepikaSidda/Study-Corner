"""Measure the ask-the-lecture agent with Strands Evals.

Runs a set of student questions against a processed lecture and scores each
answer twice:

  OutputEvaluator      LLM judge: is the answer correct, grounded in the
                       lecture, cited, and does it refuse what the lecture
                       does not cover?
  TrajectoryEvaluator  did the agent look the answer up with its tools
                       before answering?

Plus checks in plain code: how many cited times survived the citation check,
and how often steering had to correct the agent.

The default cases are written for the sample lecture (tools/make_sample_lecture.py).
For another lecture, pass --cases with your own JSON file:
    [{"name": "...", "input": "question", "expected_output": "...", "in_scope": true}]

Run:
    $env:LECTURE_AWS_PROFILE = "default"
    python tools/evaluate.py --job output/photosynthesis
Needs: pip install strands-agents-evals
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strands_evals import Case, Experiment  # noqa: E402
from strands_evals.evaluators import OutputEvaluator, TrajectoryEvaluator  # noqa: E402

from lecture_agent import chat, config  # noqa: E402

SAMPLE_CASES = [
    {"name": "stomata", "input": "Where does carbon dioxide get into the leaf?",
     "expected_output": "Through tiny holes in the leaf called stomata, with a cited time.", "in_scope": True},
    {"name": "three-needs", "input": "What do plants need for photosynthesis?",
     "expected_output": "Sunlight, water and carbon dioxide, with a cited time.", "in_scope": True},
    {"name": "equation", "input": "What does the photosynthesis equation say?",
     "expected_output": "Six carbon dioxide and six water make one glucose and six oxygen, with a cited time.", "in_scope": True},
    {"name": "chlorophyll", "input": "Why are leaves green?",
     "expected_output": "Because of chlorophyll, a pigment in the chloroplasts that traps sunlight energy, with a cited time.", "in_scope": True},
    {"name": "importance", "input": "Why does photosynthesis matter for us?",
     "expected_output": "It gives us the oxygen we breathe and makes the food almost every living thing depends on, with a cited time.", "in_scope": True},
    {"name": "out-of-scope-history", "input": "Who won the Second World War?",
     "expected_output": "Says the lecture does not cover this. Gives no answer from outside knowledge.", "in_scope": False},
    {"name": "out-of-scope-nearby", "input": "How does cellular respiration work in animals?",
     "expected_output": "Says the lecture does not cover this, even though it is a related biology topic. Gives no outside facts.", "in_scope": False},
]

OUTPUT_RUBRIC = """Score the study helper's answer to a student's question about one lecture.
Pass only if ALL of these hold:
- For a question the lecture covers: the facts match the expected output, and at least one
  time is cited in square brackets like [00:18].
- For a question the lecture does not cover: the answer says the lecture does not cover it,
  and gives NO facts from outside knowledge. Pointing to the closest part of the lecture is fine.
- Nothing is stated that the lecture did not say.
- The answer is short and in simple words.
Ignore differences in wording. Score 0-1 on correctness and grounding."""

TRAJECTORY_RUBRIC = """Pass if the agent used at least one lecture tool (search_transcript, read_transcript,
read_board or list_sections) before answering. For a question the lecture does not cover,
one search followed by a refusal is the ideal path. Answering with no tool call fails.
Score 0-1."""


def run(job_dir: Path, cases_file: Path | None, out_path: Path) -> int:
    specs = json.loads(cases_file.read_text(encoding="utf-8")) if cases_file else SAMPLE_CASES
    who = config.whoami()
    print(f"AWS account {who['account']}, model {who['model']}, lecture {job_dir}")

    run_id = uuid.uuid4().hex[:8]
    extra: dict[str, dict] = {}

    def task(case: Case) -> dict:
        student = f"eval-{run_id}-{case.name}"[:64]  # fresh session per case, no shared memory
        result = chat.ask(job_dir, student, str(case.input))
        trajectory = [t["tool"] for t in result["tools"]]
        extra[case.name] = {
            "citations": [c["label"] for c in result["citations"]],
            "removed": result["unverified_citations_removed"],
            "corrections": len(result["steering_corrections"]),
            "trajectory": trajectory,
        }
        print(f"  [{case.name}] tools={trajectory} cited={extra[case.name]['citations']}")
        return {"output": result["answer"], "trajectory": trajectory}

    cases = [
        Case(name=s["name"], input=s["input"], expected_output=s["expected_output"],
             expected_trajectory=["search_transcript"], metadata={"in_scope": s.get("in_scope", True)})
        for s in specs
    ]
    judge = config.model(temperature=0.0)
    experiment = Experiment(
        cases=cases,
        evaluators=[
            OutputEvaluator(rubric=OUTPUT_RUBRIC, model=judge, name="answer"),
            TrajectoryEvaluator(rubric=TRAJECTORY_RUBRIC, model=judge, name="looked_it_up"),
        ],
    )
    try:
        report = experiment.run_evaluations(task)
    finally:
        # Evaluation sessions are throwaway; do not leave them in the lecture folder.
        for folder in (job_dir / "chat").glob(f"session_chat-eval-{run_id}-*"):
            shutil.rmtree(folder, ignore_errors=True)

    rows = []
    for row, score, passed, reason in zip(report.cases, report.scores, report.test_passes, report.reasons):
        rows.append({"case": row["name"], "evaluator": row["evaluator"], "score": score, "pass": passed,
                     "answer": row.get("actual_output"), "reason": reason})

    print(f"\n{'case':24} {'answer':>7} {'looked up':>10}  cited")
    by_case: dict[str, dict] = {}
    for r in rows:
        by_case.setdefault(r["case"], {})[r["evaluator"]] = r
    for name, evals in by_case.items():
        a, t = evals.get("answer", {}), evals.get("looked_it_up", {})
        mark = lambda e: f"{e.get('score', 0):.2f}{'' if e.get('pass') else ' ✗'}"
        print(f"{name:24} {mark(a):>7} {mark(t):>10}  {extra.get(name, {}).get('citations')}")

    in_scope = [s["name"] for s in specs if s.get("in_scope", True)]
    cited_in_scope = sum(1 for n in in_scope if extra.get(n, {}).get("citations"))
    summary = {
        "overall_score": report.overall_score,
        "passed": sum(1 for p in report.test_passes if p),
        "evaluations": len(report.test_passes),
        "in_scope_answers_with_a_verified_citation": f"{cited_in_scope}/{len(in_scope)}",
        "unverified_citations_removed": sum(len(e["removed"]) for e in extra.values()),
        "steering_corrections": sum(e["corrections"] for e in extra.values()),
    }
    print("\n" + json.dumps(summary, indent=1))
    for r in rows:
        if not r["pass"]:
            print(f"\nFAILED {r['case']} / {r['evaluator']}: {r['reason'][:300]}\n  answer: {r['answer']}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"summary": summary, "results": rows, "details": extra}, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"\nreport: {out_path}")
    return 0 if summary["passed"] == summary["evaluations"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job", type=Path, default=ROOT / "output" / "photosynthesis", help="folder with lecture.json")
    parser.add_argument("--cases", type=Path, help="JSON file of cases (default: built-in sample-lecture cases)")
    parser.add_argument("--out", type=Path, default=ROOT / "output" / "evals" / "chat_report.json")
    args = parser.parse_args()
    sys.exit(run(args.job.resolve(), args.cases, args.out))
