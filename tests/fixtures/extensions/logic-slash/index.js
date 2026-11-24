import { SlashCommandParser } from '../../../../scripts/slash-commands/SlashCommandParser.js';
import { SlashCommand } from '../../../../scripts/slash-commands/SlashCommand.js';

SlashCommandParser.addCommandObject(SlashCommand.fromProps({
  name: 'hello',
  helpString: 'say hello',
  callback: (_named, args) => `hello ${args.join(' ')}`.trim(),
}));
