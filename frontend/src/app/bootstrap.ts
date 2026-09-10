export type Session = {
  id: string;
};

export type BootstrapState =
  | { status: 'initializing' }
  | { status: 'ready'; session: Session | null }
  | { status: 'recoverable_failure' };

export type BootstrapDependency = () => Promise<Session | null>;

/** Phase 1 deliberately has no persistence dependency; auth restoration arrives in Phase 2. */
export const defaultBootstrap: BootstrapDependency = async () => null;

export async function runBootstrap(
  dependency: BootstrapDependency,
): Promise<Session | null> {
  return dependency();
}
