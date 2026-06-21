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


**Update 2**: Just finished implementing this new method and the output is definitely better. But I'm noticing that there are these weird jump cuts that happen many times per second. I had a feeling that this would happen, and I believe it stems from the phase difference between the end of the compressed wave, and the beginning of the copied wave. There's a couple options to make it sound right:

- Check for a displacement difference and if its outside some tolerance, manually insert an interpolating line between the two points.
- Again check for the displacement, and slide the copied waveform back until its aligned with the compressed one
- Calculate the remaining piece of the wave (beginning phase to end phase) and insert it.

The second method is simple, but there's already a small shortening of the duration my algorithm applies, I don't want to increase that. The first and third methods are both better I believe.

Determining the tolerance will require info about the incoming wave's amplitude. Since all three methods will require this amplitude, I might as well go with the last one because it will give me the best output. The small wave simulation on top should not be an issue since the sampling rate is nowhere near the clock speed.

**Update 3**: I asked AI about the issue and it told me about crossfading. This is where you let the two clips overlap while simulatneosly decreasing one's volume and increasing the other to create a seamless transition. The thing is you can't just linearly decrease one and linearly increase the other, because sound volume is logarithmic. Two half volume audios added together is still 3 dB under the original volume. What we need is for the sums of the squares of the gains to always equal 1. We can easily produce this by using a cosine wave for one clip's gain, and a sine wave for the other. Since AI gave me the answer, I'll at least try to think of my own implementation. 

Just finished implementing that and finally the output sounds almost perfect. The only thing is that there's still some tiny amount of like noise or something in the background which I gotta investigate. Maybe I can find a way to determine its frequency, and just filter it out?

**Update 4**: Saying it sounds almost perfect was an exaggeration. The crossfade while it does help with the symptoms, does not actually fix the root problem which is phase jumps. I asked AI and it told me about a method similar to a copying method I was already thinking about. After compressing, instead of just taking the last N samples of the clip and appending it, we create an N sample wide sliding window and compare the beginning of this window to the end of the clip, if the point matches then we can copy. However the AI took it a step further and mentioned that the slopes need to match too, which honestly I wish I thought've myself bc its such a logical next step.

**Update 5**: This method wasn't sitting right with me. I remembered that when I tried to use any part of the chunk other than the one at the end, the output sounded bad. Now I'm about to do that again? After rereading what the AI told me I realized I'm not actually supposed to slide a window across the whole chunk. It's more like I keep one end of the window fixed to the end of the chunk, and expand/shrink that window on the other side. I only calculate cross-correlation on the section that will overlap and an equally sized piece of the end of the chunk.

**Update 6**: this problem with the compress + copy method honestly seems fundamental to the method. I was looking at another method to pitch shift and it was way simpler. You have a circular buffer that the live recording gets written to, as well as a read head that moves faster/slower depending on if we want to pitch shift up/down. Because this buffer is circular, eventually the write head will either lap the read head, or it will get lapped by the read head. To resolve this, when the distance between the read and write heads gets smaller than some tolerance, we just move the read head back 1 period if its behind, or forward 1 period if its ahead. If we keep this circular buffer sufficiently small, there shouldn't be any kind of echo happening, and we can guarantee that there won't be any jumps in phase (which plagued our previous method).

We use the value of the target frequency over the current vocal frequency to decide the speed of the read head relative to the write head. We can obtain the current vocal frequency using existing pitch detection algoriths. For both the pitch shifting, and detection I plan on seeing the design and figuring out my own implementation.
