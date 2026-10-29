"""Small desktop controls shared by invoice lists."""
import tkinter as tk


def checkbox_icons(master, size=24):
    images = []
    for checked in (False, True):
        image = tk.PhotoImage(master=master, width=size, height=size)
        image.put('#4169a1', to=(1, 1, size-1, size-1))
        image.put('#4169a1' if checked else '#ffffff', to=(3, 3, size-3, size-3))
        if checked:
            # Thick white tick, sized for the 32-pixel invoice rows.
            for x in range(5, 10):
                y = x + 6
                image.put('#ffffff', to=(x, y-1, x+2, y+2))
            for x in range(10, 20):
                y = 25 - x
                image.put('#ffffff', to=(x, y-1, x+2, y+2))
        images.append(image)
    return tuple(images)
