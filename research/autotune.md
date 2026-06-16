# Autotune Research

June 15, 2026

Autotune is an effect that simply pitch shifts vocals up or down so that it hits the frequency of some note, like 440 Hz which corresponds to A4.

At Nokia I read that you can represent a complex exponential using a sin and cos wave (remember that formula, turns out people use it). I also remember from calculus that you can horizontally shift the fourier transform of some function by multiplying by a complex exponential. Whatever the coefficient of $i$ is becomes what you shift by.

This TRANSLATES to pitch shifting vocals since what we essentially wanna do is change someone's pitch without them sounding like a chipmunk. That means we don't wanna change the shape of the fourier transform, only where "humps" are.

So what we're going to do is from the beginning of my recording I'm gonna use the sample rate and the numebr of the sample to see what my time is and evaluate both my cosine wave and sine wave, at those timesteps. Then, I'll multiply the vocal sample by each wave and add it together, and save that as the new sample. We'll play it to see if I was able to pitch shift it properly.

**Update**: Just tested it and it sounds super metallic and just compeltely inhuman. I asked Gemini about it and it told me that frequency shifting breaks the ratio between harmonics, and the human voice is characterized by it being composed of harmonics. Its usually metallic objects that emit inharmonics, which is why I interpreted the sound as metallic. Instead of horizontally translating the fourier transform graph, I need to horizontally stretch it. Unfortunately, there aren't any shift theorems that accomplish that so ima have to figure out something else. Like compressing the audio and then copying it so that its back at the length it was pre compression. The thing is this can no longer be odne sample by sample, I gotta process them in groups. Regardless, the plan is as follows:

1. Collect N amount of samples
2. Reinterpret the time duration of these samples as something smaller/larger depending on if we wanna pitch shift up/down.
3. Copy/truncate the samples so that they fulfill the original time duration.
3. Resample at the original sample rate.

Now the question is if this works, can I think of another maybe better (or better in a different way) method?

