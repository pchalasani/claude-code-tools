import type { Composer } from "./composer";
import type { Row } from "./outline";
export interface WorkingSources {
  composer: Composer;
}
export function isWorking(state: WorkingSources, row: Row): boolean {
  if (state.composer.sendingAt(row.id)) {
    return true;
  }
  if (state.composer.signalWorkingAt(row.id)) {
    return true;
  }
  if (row.kind === "thread" && row.awaiting) {
    if (
      row.parentId !== null
      && state.composer.signalWorkingAt(row.parentId)
      && state.composer.selectedSignalAt(row.parentId) === row.label
    ) {
      return false;
    }
    return true;
  }
  return state.composer.pendingAt(row.id).length > 0;
}
