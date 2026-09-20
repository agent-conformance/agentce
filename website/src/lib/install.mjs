// The Install section's single source of truth: which command each per-ecosystem tab shows.
//
// `data/release-state.json` says which distribution channels are published. A tab shows a channel's
// zero-install one-liner only when the channel is published, and the command that works from a checkout
// today otherwise, so a page can never advertise an install path that does not yet exist. The Install
// component renders from `installTabs`, and `scripts/check-install.mjs` proves the gate both ways.

// One entry per tab. `channel` is the release-state channel that gates it; `key` names the entry field
// that holds the tab's one-liner (`null` means the tab always shows the command that works from a
// checkout, which does not depend on any registry).
export const TABS = [
  { id: 'uv', label: 'uv', channel: 'pypi', key: null, summary: 'From a checkout, with uv' },
  { id: 'uvx', label: 'uvx', channel: 'pypi', key: 'one_liner', summary: 'Zero-install, with uvx' },
  { id: 'pipx', label: 'pipx', channel: 'pypi', key: 'pipx_one_liner', summary: 'Zero-install, with pipx' },
  { id: 'npx', label: 'npx', channel: 'npm', key: 'one_liner', summary: 'Zero-install, with npx' },
  {
    id: 'docker',
    label: 'docker',
    channel: 'container-registry',
    key: 'one_liner',
    summary: 'Zero-install, with docker',
  },
];

// What one tab shows for a release state: the command, whether it is the published one-liner, and the
// artifact it names.
export function resolveTab(state, tab) {
  const entry = state.channels[tab.channel];
  const oneLiner = tab.key === null ? null : entry[tab.key];
  const published = entry.published === true && typeof oneLiner === 'string' && oneLiner !== '';
  return {
    id: tab.id,
    label: tab.label,
    summary: tab.summary,
    channel: tab.channel,
    gated: tab.key !== null,
    artifact: entry.artifact,
    published,
    command: published ? oneLiner : entry.today,
  };
}

export function installTabs(state) {
  return TABS.map((tab) => resolveTab(state, tab));
}
