# Applying `pipecat.mdx` to jambonz-fern-config

Dave offered either a markdown file or a PR to
[jambonz/jambonz-fern-config](https://github.com/jambonz/jambonz-fern-config).
`docs/pipecat.mdx` is written in that repo's house style — front-matter with
`title` and `subtitle`, internal links like `/verbs/verbs/listen`, and their
`<Note>` / `<Warning>` / `<Tip>` components — so it can go straight in.

Two steps, from this directory:

```sh
gh repo fork jambonz/jambonz-fern-config --clone --remote=false
cd jambonz-fern-config
git checkout -b docs/pipecat
cp ../docs/pipecat.mdx fern/docs/pages/tutorials/pipecat.mdx
```

Then register it in `fern/docs.yml`, under `tab: tutorials` →
`section: Voice AI Examples`. The list is alphabetical, so it goes between
`OpenAI Realtime API` and `Qwen Omni Realtime`:

```yaml
          - page: OpenAI Realtime API
            path: ./docs/pages/tutorials/openai.mdx
          - page: Pipecat
            path: ./docs/pages/tutorials/pipecat.mdx
          - page: Qwen Omni Realtime
            path: ./docs/pages/tutorials/qwen.mdx
```

Check it renders before pushing — their CLAUDE.md gives the command:

```sh
npm install -g fern-api
fern docs dev
```

Then commit, push and open the PR against `jambonz/jambonz-fern-config`.

## Placement is a judgement call worth flagging in the PR

"Voice AI Examples" currently holds vendors — AssemblyAI, Deepgram, ElevenLabs,
Gemini, OpenAI, Qwen, xAI, Ultravox — all speech-to-speech providers. Pipecat is
a framework, not a vendor, so it is the odd one out there.

The alternative is Guides → Features, beside `Voice Agents`, which documents the
`agent` verb. That page is about jambonz orchestrating the turn cycle itself;
this one is about handing that job to an external framework. Adjacent, but a
different thing.

Tutorials is the better fit because the page is a worked integration example
with runnable code, which is what that tab is for. But say so in the PR body and
offer to move it — it costs one line and it is his site.
