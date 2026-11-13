package com.aisignx.player.ui

/** Pure checks used by [PlayerWebClient], kept free of android.* so JVM tests can run them. */
object PlayerCheck {

    /** Evaluated in the page: the state player.js writes to body[data-state], or "". */
    const val STATE_JS = "(function(){return (document.body && document.body.dataset.state) || '';})()"

    /**
     * Whether the evaluateJavascript() result of [STATE_JS] shows a running player.
     * The result is JSON: "\"playing\"" when running; "\"\"" or "null" when the
     * scripts never ran (an error page, or a snapshot whose scripts are missing).
     */
    fun isRunning(jsResult: String?): Boolean {
        val s = jsResult?.trim()?.removeSurrounding("\"").orEmpty()
        return s.isNotEmpty() && s != "null" && s != "undefined"
    }

    /** Main-frame statuses handled as a failed load: any 5xx (500, 502-504, 520-530 from proxies). */
    fun isServerError(status: Int): Boolean = status in 500..599
}
