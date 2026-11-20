/** Hex values (charts append alpha suffixes such as `${color}44`). Match status colours mirror the CSS tokens. */
export const colors = {
  blue: '#0a3be0', cyan: '#14b8d4', navy: '#13317f', amber: '#f59e0b', red: '#ef4444', green: '#10b981',
  violet: '#7c3aed', pink: '#ec4899', teal: '#0d9488', orange: '#f97316', slate: '#64748b', ink: '#1e293b', grid: '#e8edf5',
  hot: '#dc2626', warm: '#f59e0b', custom: '#7c3aed', unmatched: '#94a3b8',
  ready: '#10b981', onHold: '#f59e0b', sold: '#0a3be0',
};
/** Series colours for Hot / Warm / Custom / No match, in that order everywhere. */
export const statusColors = { hot: colors.hot, warm: colors.warm, custom: colors.custom, unmatched: colors.unmatched } as const;
export const categorical = [colors.blue, colors.cyan, colors.amber, colors.violet, colors.green, colors.pink, colors.orange, colors.teal, colors.slate];
