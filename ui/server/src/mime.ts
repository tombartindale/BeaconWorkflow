// Content types for the files the UI serves. The same answers Python's mimetypes gave,
// including leaving unknown extensions (.toml) as octet-stream.
const TYPES: Record<string, string> = {
  '.html': 'text/html', '.htm': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.json': 'application/json', '.map': 'application/json', '.txt': 'text/plain', '.csv': 'text/csv',
  '.md': 'text/markdown', '.srt': 'application/x-subrip', '.vtt': 'text/vtt', '.xml': 'application/xml',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.svg': 'image/svg+xml',
  '.webp': 'image/webp', '.ico': 'image/vnd.microsoft.icon',
  '.pdf': 'application/pdf', '.zip': 'application/zip',
  '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm',
  '.mp3': 'audio/mpeg', '.wav': 'audio/x-wav', '.m4a': 'audio/mp4',
  '.otf': 'font/otf', '.ttf': 'font/ttf', '.woff': 'font/woff', '.woff2': 'font/woff2',
};

export function mimeType(name: string): string {
  const i = name.lastIndexOf('.');
  return (i >= 0 && TYPES[name.slice(i).toLowerCase()]) || 'application/octet-stream';
}

/** Text types carry a charset, as the Python backend's static files did. */
export function withCharset(type: string): string {
  return type.startsWith('text/') || type.endsWith('javascript') ? `${type}; charset=utf-8` : type;
}
