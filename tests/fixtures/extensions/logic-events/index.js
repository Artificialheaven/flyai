import { eventSource, event_types, extension_settings, saveSettingsDebounced } from '../../../../script.js';

extension_settings['logic-events'] = extension_settings['logic-events'] || { received: 0 };

eventSource.on(event_types.MESSAGE_RECEIVED, () => {
  extension_settings['logic-events'].received += 1;
  saveSettingsDebounced();
});
