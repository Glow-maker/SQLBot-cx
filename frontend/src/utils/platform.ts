export const isPlatformLogicalDatasourceEnabled = () => {
  return String(import.meta.env.VITE_PLATFORM_LOGICAL_DATASOURCE_ENABLED ?? 'false').toLowerCase() === 'true'
}
