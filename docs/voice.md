# How our Voices Work

Before we can understand autotune and other effects, we gotta be familiar with the challenges of dealing with human voices, as we don't just emit a single pure sine tone when we speak.

![Fast Fourier Transform of human voice](./docs/assets/voice_transform.jpg)

Above is an example of the Fourier Transform of someone's voice. On the horizontal axis you have frequency, and on the vertical you have amplitude. As you can see there's a non-zero amount of practically every frequency. What this graph tells us is that the human voice consists of a bunch of harmonics. And the pitch your ears end up hearing is decided by the fundamental frequency (the frequency of the leftmost hump).

The heights of these humps also decides the timbre, uniquely identifying a person's voice.
