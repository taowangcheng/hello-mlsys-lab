// Add the third DAG reference in its own batch, within the 12-run simulation limit.
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
export function verifyCandidates(expected, actual) {
  execFileSync(
    path.join(project, '.venv/bin/python'),
    [
      '-c',
      [
        'import json, sys',
        'from mlab.contracts import Candidate',
        'def normalize(items):',
        '    result = {}',
        '    for item in items:',
        '        value = Candidate.model_validate(item).model_dump(mode="json")',
        '        result[value["id"]] = {key: value[key] for key in ("infra", "strategy")}',
        '    return result',
        'expected, actual = json.load(sys.stdin)',
        'assert normalize(expected) == normalize(actual), "Unexpected configuration changes"',
      ].join('\n'),
    ],
    { cwd: project, input: JSON.stringify([expected, actual]) },
  );
}

export async function addReferenceExperiment({
  page,
  base,
  session,
  view,
  speak,
  screenshot,
  until,
}) {
  const findReference = (state) =>
    state.experiments.find(
      (e) =>
        e.session === session &&
        e.author === 'assistant' &&
        e.candidates.length === 1 &&
        e.candidates[0].id === 'tp2_bf16',
    );
  let state = await view();
  let reference = findReference(state);
  if (!reference) {
    await speak(
      'Please add the unchanged tp2_bf16 task example as a separate one-configuration draft named Tensor parallel reference. I will run it myself, then compare it with the previous two designs. Explain in under 80 words why this broader architectural reference does not isolate the effect of precision.',
      '09-reference-proposal',
    );
    state = await view();
    reference = findReference(state);
  }
  if (!reference) throw Error('The tensor-parallel reference draft was not created');
  verifyCandidates([state.templates.find((c) => c.id === 'tp2_bf16')], reference.candidates);
  if (reference.editable) {
    await page.goto(`${base}/#/9.3/plans/${encodeURIComponent(reference.key)}`);
    await page.locator('.editor-title > summary').click();
    await page
      .getByRole('textbox', { name: 'What do you expect?', exact: true })
      .fill(
        'This reference changes placement, parallelism, precision and scheduling. I will compare the complete designs without attributing the difference to one parameter.',
      );
    await page.getByRole('spinbutton', { name: 'Repetitions', exact: true }).fill('2');
    for (const checkbox of await page.locator('.workload-options input').all())
      await checkbox.check();
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await screenshot('09-reference-experiment');
    await page.getByRole('button', { name: 'Run simulations', exact: true }).click();
    await page.getByRole('button', { name: 'Start simulations', exact: true }).click();
  }
  state = await until(async () => {
    const value = await view();
    const runs = value.runs.filter((r) => r.session === session && r.owner === reference.id);
    if (runs.some((r) => ['failed', 'cancelled', 'timed_out'].includes(r.status)))
      throw Error('A reference simulation failed');
    return runs.length === 6 && runs.every((r) => r.status === 'succeeded') ? value : false;
  });
  await page.goto(`${base}/#/9.3/compare`);
  const clear = page.getByRole('button', { name: 'Clear selection', exact: true });
  if (await clear.isEnabled()) await clear.click();
  await page.goto(`${base}/#/9.3/runs`);
  const controls = state.runs.filter(
    (r) =>
      r.session === session &&
      r.role === 'simulator' &&
      r.workload === 'development' &&
      r.repeat === 0,
  );
  if (new Set(controls.map((r) => r.candidate)).size !== 3)
    throw Error('Expected three distinct reference configurations');
  for (const run of controls) await page.getByLabel(`Select ${run.id}`, { exact: true }).check();
  await page.getByRole('button', { name: 'Compare results', exact: true }).click();
  await screenshot('05-comparison');
  await page.goto(`${base}/#/9.3/runs`);
  return state;
}
