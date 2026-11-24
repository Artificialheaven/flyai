import { extension_settings, saveSettingsDebounced } from '../../../../script.js';

extension_settings['logic-panel'] = extension_settings['logic-panel'] || { greeting: 'hello', limit: 10, enabled: true, mode: 'a' };
saveSettingsDebounced();
