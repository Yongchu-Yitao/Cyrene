package ai.cyrene.mobile.desktop

import android.graphics.Canvas
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.drawable.Drawable
import android.view.View

/** Extends the web drawer backdrop across native system insets, without dimming the drawer. */
class SystemInsetScrim(private val decor: View, private val content: View) : Drawable(), AutoCloseable {
    private val paint = Paint().apply { color = 0x38000000 }
    private var shown = false
    private val layout = View.OnLayoutChangeListener { _, _, _, _, _, _, _, _, _ ->
        setBounds(0, 0, decor.width, decor.height)
        invalidateSelf()
    }

    init {
        decor.addOnLayoutChangeListener(layout)
        content.addOnLayoutChangeListener(layout)
    }

    fun show(open: Boolean) {
        if (shown == open) return
        shown = open
        if (open) {
            setBounds(0, 0, decor.width, decor.height)
            decor.overlay.add(this)
        } else decor.overlay.remove(this)
    }

    override fun draw(canvas: Canvas) {
        val origin = IntArray(2); val position = IntArray(2)
        decor.getLocationOnScreen(origin); content.getLocationOnScreen(position)
        val left = (position[0] - origin[0]).toFloat()
        val top = (position[1] - origin[1]).toFloat()
        val right = left + content.width; val bottom = top + content.height
        val width = bounds.width().toFloat(); val height = bounds.height().toFloat()
        canvas.drawRect(0f, 0f, width, top, paint)
        canvas.drawRect(0f, bottom, width, height, paint)
        canvas.drawRect(0f, top, left, bottom, paint)
        canvas.drawRect(right, top, width, bottom, paint)
    }

    override fun setAlpha(alpha: Int) { paint.alpha = alpha; invalidateSelf() }
    override fun setColorFilter(filter: ColorFilter?) { paint.colorFilter = filter; invalidateSelf() }
    @Deprecated("Required by Drawable")
    override fun getOpacity() = PixelFormat.TRANSLUCENT

    override fun close() {
        show(false)
        decor.removeOnLayoutChangeListener(layout)
        content.removeOnLayoutChangeListener(layout)
    }
}
