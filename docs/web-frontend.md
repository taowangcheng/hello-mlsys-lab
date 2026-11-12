# Later plan for the web front end

The current front end is handwritten plain JS with no build step (`app.js`, `charts.js` and `app.css` under `src/mlab/web/static/`); the server is Starlette. This document records the plan and the trade-offs for rewriting the front end with open-source components. The API is in `docs/web-api.md`.

## Existing open-source products

No open-source product can serve as this lab bench as it is. The most laborious parts are all in `mlab` and the server: the student and the agent going through the two flywheels by the rules, the summaries on confirmation cards computed by `mlab`, the student's own words that forms write into records, and the records of earlier experiments. Whichever product is used, these parts have to be connected by hand.

| Category | Examples | What comes ready-made | What does not fit |
| --- | --- | --- | --- |
| Web UIs for Claude Code | claudecodeui, claude-code-webui, cui | Chat, streaming output, session management | Built for developers, with a terminal, file tree and git; claudecodeui reads and writes the user's own `~/.claude` configuration, which conflicts with configuring each task separately, and is licensed under AGPL-3.0; cui was archived in March 2026 |
| Chat component libraries | assistant-ui, CopilotKit (React), Chainlit (Python) | Chat panel, tool-call display, approval buttons | Only components, which have to be wired to the existing API; Chainlit has been maintained by volunteers since May 2025 |
| Experiment tracking | MLflow | Run lists, cross-run comparison, metric charts; can record Claude Code sessions through hooks | Knows only runs and metrics, not hypotheses, comments, approvals or comparisons of simulation and measurement; adds a second interface beside the course page |
| Notebooks with an agent panel | marimo, JupyterLab (jupyterlab-acp) | Chat with Claude Code next to the notebook | marimo's agent panel is still experimental; no forms or confirmation cards |
| General chat platforms | Open WebUI, LibreChat | A complete chat interface | Call the model API directly rather than through Claude Code, so permission rules, hooks and `AGENTS.md` have no effect |

## Plan: React with open-source components

The front end is rewritten as a whole. The server, all `/api` endpoints, `mlab`, the agent for each task and their tests stay unchanged, and so do the three-column layout and the eight interaction patterns (`docs/plan.md` section 9).

| Part | What to use |
| --- | --- |
| UI components | shadcn/ui: buttons, inputs, cards, tabs, tables, resizable panes, light and dark themes |
| Chat panel | assistant-ui's ExternalStoreRuntime: the server keeps the messages, and the component only displays them; tool calls, confirmation cards and question cards go into its message stream |
| Data loading | TanStack Query: caching, retries and polling, replacing the handwritten loading and caching logic |
| Charts | Recharts (the shadcn/ui chart components); the request timeline is drawn by hand |
| Icons | Lucide |
| Build | Vite; the build output goes into `src/mlab/web/static/` and ships with `mlab` |

All of these components use MIT or an equally permissive license.

Constraints:

- Development needs Node 24 LTS or later; students do not need Node.
- As before, the page loads no external resources and has no inline scripts; data is inserted as plain text, without `dangerouslySetInnerHTML`. Styles that the component libraries insert at run time must pass the CSP.
- `tests/test_web_static.py` changes to check the front-end source and the build output.

Verification:

- With the fake agent, go through the 12 steps of `docs/demo.md` offline in a headless browser; look at every kind of record in all three tasks, in both light and dark themes.
- `tests/test_web_api.py` and `evals/web_story.py` use only the API and still pass.
- The new front end replaces the current one only after it passes these checks.

## A lighter option

Add no dependencies and only tidy up the current styles: bring the shadcn/ui colors, corner radii and spacing into `app.css`, and render the agent's replies as full Markdown. Basecoat provides shadcn-style components without React, but it depends on a Tailwind build, so it saves little work.

## Sources

- Web UIs for Claude Code: [claudecodeui](https://github.com/siteboon/claudecodeui), [claude-code-webui](https://github.com/sugyan/claude-code-webui), [cui](https://github.com/wbopan/cui)
- Chat component libraries: [assistant-ui's ExternalStoreRuntime](https://www.assistant-ui.com/docs/runtimes/custom/external-store), [CopilotKit's useHumanInTheLoop](https://docs.copilotkit.ai/reference/hooks/useHumanInTheLoop), [Chainlit](https://pypi.org/project/chainlit/)
- Experiment tracking: [MLflow tracing for Claude Code](https://mlflow.org/docs/latest/genai/tracing/integrations/listing/claude_code)
- Notebooks: [marimo's agent panel](https://docs.marimo.io/guides/editor_features/agents/), [jupyterlab-acp](https://pypi.org/project/jupyterlab-acp/)
- Components and tools: [shadcn/ui](https://ui.shadcn.com), [TanStack Query](https://tanstack.com/query), [Recharts](https://recharts.org), [Lucide](https://lucide.dev), [Vite](https://vite.dev), [Basecoat](https://basecoatui.com/introduction/)
