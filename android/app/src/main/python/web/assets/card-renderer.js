/* Character-card Markdown/HTML presentation; card scripts are never evaluated. */
(function () {
  'use strict';
  var styles = new Set(('color background background-color background-image background-size background-repeat background-position font font-size font-weight font-style font-family line-height text-align text-decoration white-space word-break border border-color border-width border-style border-radius padding padding-left padding-right padding-top padding-bottom margin margin-left margin-right margin-top margin-bottom width height min-width max-width min-height max-height display flex flex-direction flex-wrap align-items justify-content gap grid-template-columns overflow overflow-x overflow-y opacity box-shadow').split(' '));
  DOMPurify.addHook('afterSanitizeAttributes', function (node) {
    if (node.style) {
      Array.from(node.style).forEach(function (property) {
        var value = node.style.getPropertyValue(property);
        var unsafeUrl = Array.from(value.matchAll(/url\(\s*['"]?([^'")]+)['"]?\s*\)/gi)).some(function (match) {
          try { return !['http:','https:'].includes(new URL(match[1], location.href).protocol); } catch (_) { return true; }
        });
        if (!styles.has(property) || unsafeUrl || /expression|behavior|javascript|@import/i.test(value)) node.style.removeProperty(property);
      });
    }
    if (node.tagName === 'A') { node.setAttribute('rel', 'noopener noreferrer'); node.setAttribute('target', '_blank'); }
    if (node.tagName === 'IMG') { node.setAttribute('loading', 'lazy'); node.setAttribute('referrerpolicy', 'no-referrer'); }
  });
  var cache = new Map();
  window.NovelCardUI = {
    render: function (value) {
      var text = NovelUI.textContent(value);
      if (cache.has(text)) return cache.get(text);
      // These are author-defined wrappers around ordinary Markdown/status blocks.
      var source = text.replace(/<\/?(?:status|statusblock|role_content)>/gi, '\n');
      var html = DOMPurify.sanitize(marked.parse(source, {gfm:true, breaks:true}), {
        FORBID_TAGS:['script','iframe','object','embed','form','input','style','link','meta'],
        FORBID_ATTR:['srcdoc','autofocus','formaction'], ADD_ATTR:['data-message']
      });
      if (cache.size >= 200) cache.delete(cache.keys().next().value);
      cache.set(text, html);
      return html;
    }
  };
})();
