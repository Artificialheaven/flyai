import { extension_settings, saveSettingsDebounced } from '../../../../script.js';

extension_settings['logic-settings'] = extension_settings['logic-settings'] || { enabled: true };
saveSettingsDebounced();
