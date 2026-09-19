// Fixture mode: `?fixture=scenario` (or `?fixture=scenario-failed`) makes Analyze Response
// replay a recorded run instead of calling the backend. The replay code and the fixture JSON
// live in ./replay.ts, which is only ever loaded with a dynamic import() in this mode.

export type FixtureMode = 'scenario' | 'scenario-failed'

export const FIXTURE_MODE: FixtureMode | null = (() => {
  const value = new URLSearchParams(window.location.search).get('fixture')
  return value === 'scenario' || value === 'scenario-failed' ? value : null
})()
