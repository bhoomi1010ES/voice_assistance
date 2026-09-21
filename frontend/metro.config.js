const { getDefaultConfig, mergeConfig } = require('@react-native/metro-config');
const fs = require('fs');
const path = require('path');

const REACT_NATIVE_ROOT = path.resolve(__dirname, 'node_modules/react-native');

function resolveReactNativePrivateModule(moduleName) {
  if (!moduleName.startsWith('react-native/src/private/')) {
    return null;
  }

  const relativePath = moduleName.slice('react-native/'.length);
  const basePath = path.resolve(REACT_NATIVE_ROOT, relativePath);
  const candidates = [
    basePath,
    `${basePath}.js`,
    path.join(basePath, 'index.js'),
  ];
  const filePath = candidates.find(candidate => fs.existsSync(candidate));
  return filePath ? { type: 'sourceFile', filePath } : null;
}

/**
 * Metro 0.87 enables package exports by default, but react-native's
 * package.json does not export `./src/private/*`. Falling back for
 * `ReactNativeFeatureFlags` on every transform floods the log and can stall
 * the first bundle past the 60s inspector heartbeat.
 *
 * @type {import('@react-native/metro-config').MetroConfig}
 */
const config = {
  resolver: {
    resolveRequest: (context, moduleName, platform) => {
      const privateModule = resolveReactNativePrivateModule(moduleName);
      if (privateModule) {
        return privateModule;
      }
      return context.resolveRequest(context, moduleName, platform);
    },
  },
};

module.exports = mergeConfig(getDefaultConfig(__dirname), config);
