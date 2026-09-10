const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const sourceDirectories = [
  path.join(root, 'frontend', 'src'),
  path.join(root, 'frontend', '__tests__'),
];
const allowedExtensions = new Set(['.js', '.jsx', '.ts', '.tsx']);
const secretPatterns = [
  /(?:sk-|xai-|AIza|AKIA)[A-Za-z0-9_-]{8,}/,
  /https?:\/\/[^\s/@]+:[^\s/@]+@/i,
];

function collectFiles(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      return collectFiles(entryPath);
    }
    return allowedExtensions.has(path.extname(entry.name)) ? [entryPath] : [];
  });
}

const violations = sourceDirectories.flatMap(collectFiles).flatMap(filePath => {
  const contents = fs.readFileSync(filePath, 'utf8');
  return secretPatterns
    .filter(pattern => pattern.test(contents))
    .map(pattern => `${path.relative(root, filePath)} matches ${pattern}`);
});

if (violations.length > 0) {
  console.error('UI secret scan failed:');
  violations.forEach(violation => console.error(`- ${violation}`));
  process.exitCode = 1;
} else {
  console.log('UI secret scan passed.');
}
